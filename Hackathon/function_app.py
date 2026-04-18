import azure.functions as func
import datetime
import json
import logging
import uuid
import os
from azure.cosmos import CosmosClient
from moderation import check_message
from email_service import send_alert_email, should_send_alert
from gmail_service import fetch_unread_emails, mark_as_read, add_label, get_email_stats

app = func.FunctionApp()

# In-memory storage for alert configuration and message history (MVP simplification)
# For production, use Cosmos DB or Azure Table Storage
alert_config = {
    "enabled": True,
    "trigger_level": "all"  # "all", "toxic_harassment", "only_toxic", "only_harassment", "only_spam"
}
message_history = []  # For MVP: simple list that persists in memory
violation_stats = {"clean": 0, "spam": 0, "toxic": 0, "harassment": 0}


def get_container(container_name="messages"):
    """Get Cosmos DB container. Returns None if unavailable (fail-open)."""
    connection_string = os.environ.get('COSMOS_CONNECTION_STRING')
    if not connection_string:
        return None
    try:
        client = CosmosClient.from_connection_string(connection_string)
        database = client.get_database_client("streamingdb")
        container = database.get_container_client(container_name)
        return container
    except Exception as e:
        logging.error(f"Failed to connect to Cosmos DB: {e}")
        return None


# ============================================================================
# MAIN MESSAGE MODERATION ENDPOINT - Used by the frontend dashboard
# ============================================================================
@app.route(route="api/moderate", methods=["POST"], auth_level=func.AuthLevel.ANONYMOUS)
def moderate_message(req: func.HttpRequest) -> func.HttpResponse:
    """
    Frontend moderation endpoint.
    Accepts a message and returns moderation result.
    
    Request body:
    {
        "content": "message text",
        "username": "optional username"
    }
    
    Response:
    {
        "is_allowed": true/false,
        "category": "clean|spam|toxic|harassment",
        "confidence": 0.0-1.0,
        "reason": "explanation",
        "id": "message_id"
    }
    """
    try:
        req_body = req.get_json()
    except ValueError:
        return func.HttpResponse(
            json.dumps({"error": "Invalid JSON in request body."}),
            status_code=400,
            mimetype="application/json"
        )

    content = req_body.get('content', '').strip()
    if not content:
        return func.HttpResponse(
            json.dumps({"error": "'content' field is required."}),
            status_code=400,
            mimetype="application/json"
        )

    username = req_body.get('username', 'Anonymous')
    message_id = str(uuid.uuid4())
    current_time = datetime.datetime.now(datetime.timezone.utc).isoformat()

    # Perform moderation
    moderation_result = check_message(content)

    # Build response
    response_data = {
        "id": message_id,
        "is_allowed": moderation_result.get("is_allowed", True),
        "category": moderation_result.get("category", "clean"),
        "confidence": moderation_result.get("confidence", 1.0),
        "reason": moderation_result.get("reason", ""),
        "username": username,
        "timestamp": current_time
    }

    # Store in message history (MVP)
    message_history.append(response_data)

    # Update stats
    category = response_data["category"]
    if not response_data["is_allowed"]:
        violation_stats[category] = violation_stats.get(category, 0) + 1
        
        # Send email alert if configured
        if should_send_alert(category, alert_config):
            send_alert_email(
                content,
                category,
                response_data["confidence"],
                response_data["reason"]
            )

        # Store violation in Cosmos DB if available
        violations_container = get_container("violations")
        if violations_container:
            violation_record = {
                "id": message_id,
                "content": content,
                "username": username,
                "category": category,
                "confidence": response_data["confidence"],
                "reason": response_data["reason"],
                "timestamp": current_time
            }
            try:
                violations_container.create_item(body=violation_record)
            except Exception as e:
                logging.error(f"Error storing violation in Cosmos DB: {e}")
    else:
        violation_stats["clean"] = violation_stats.get("clean", 0) + 1

    # Store message in Cosmos DB if available
    messages_container = get_container("messages")
    if messages_container:
        try:
            messages_container.create_item(body=response_data)
        except Exception as e:
            logging.error(f"Error storing message in Cosmos DB: {e}")

    status_code = 403 if not response_data["is_allowed"] else 200
    return func.HttpResponse(
        json.dumps(response_data),
        status_code=status_code,
        mimetype="application/json"
    )


# ============================================================================
# STATISTICS ENDPOINT - For dashboard stats cards
# ============================================================================
@app.route(route="api/stats", methods=["GET"], auth_level=func.AuthLevel.ANONYMOUS)
def get_stats(req: func.HttpRequest) -> func.HttpResponse:
    """
    Returns moderation statistics.
    
    Response:
    {
        "total_messages": count,
        "clean_messages": count,
        "violations": count,
        "category_breakdown": {...}
    }
    """
    total = len(message_history)
    clean_count = violation_stats.get("clean", 0)
    violations_count = total - clean_count
    
    stats = {
        "total_messages": total,
        "clean_messages": clean_count,
        "violations": violations_count,
        "category_breakdown": {
            "clean": clean_count,
            "spam": violation_stats.get("spam", 0),
            "toxic": violation_stats.get("toxic", 0),
            "harassment": violation_stats.get("harassment", 0)
        }
    }
    
    return func.HttpResponse(
        json.dumps(stats),
        mimetype="application/json"
    )


# ============================================================================
# MESSAGE HISTORY ENDPOINT - For dashboard message log
# ============================================================================
@app.route(route="api/violations", methods=["GET"], auth_level=func.AuthLevel.ANONYMOUS)
def get_violations(req: func.HttpRequest) -> func.HttpResponse:
    """
    Returns message history with optional filtering.
    Query parameters:
    - filter: "all" (default), "clean", "violations"
    - limit: max results (default 50)
    
    Response:
    [
        {
            "id": "uuid",
            "content": "message",
            "category": "spam|toxic|harassment|clean",
            "confidence": 0.0-1.0,
            "reason": "explanation",
            "is_allowed": true/false,
            "timestamp": "ISO datetime"
        }
    ]
    """
    filter_type = req.params.get("filter", "all")  # "all", "clean", "violations"
    limit = int(req.params.get("limit", "50"))

    filtered_history = []
    
    for msg in message_history:
        if filter_type == "violations" and msg["is_allowed"]:
            continue
        elif filter_type == "clean" and not msg["is_allowed"]:
            continue
        
        filtered_history.append(msg)
    
    # Return most recent first
    filtered_history.reverse()
    
    return func.HttpResponse(
        json.dumps(filtered_history[:limit]),
        mimetype="application/json"
    )


# ============================================================================
# ALERT CONFIGURATION ENDPOINT - For settings panel
# ============================================================================
@app.route(route="api/alerts/config", methods=["POST"], auth_level=func.AuthLevel.ANONYMOUS)
def set_alert_config(req: func.HttpRequest) -> func.HttpResponse:
    """
    Updates email alert configuration.
    
    Request body:
    {
        "enabled": true/false,
        "trigger_level": "all" | "toxic_harassment" | "only_toxic" | "only_harassment" | "only_spam"
    }
    """
    global alert_config
    
    try:
        req_body = req.get_json()
        alert_config["enabled"] = req_body.get("enabled", alert_config["enabled"])
        alert_config["trigger_level"] = req_body.get("trigger_level", alert_config["trigger_level"])
        
        return func.HttpResponse(
            json.dumps({
                "success": True,
                "config": alert_config
            }),
            mimetype="application/json"
        )
    except Exception as e:
        logging.error(f"Error updating alert config: {e}")
        return func.HttpResponse(
            json.dumps({"error": str(e)}),
            status_code=400,
            mimetype="application/json"
        )


@app.route(route="api/alerts/config", methods=["GET"], auth_level=func.AuthLevel.ANONYMOUS)
def get_alert_config(req: func.HttpRequest) -> func.HttpResponse:
    """Get current email alert configuration."""
    return func.HttpResponse(
        json.dumps(alert_config),
        mimetype="application/json"
    )


# ============================================================================
# GMAIL INTEGRATION ENDPOINTS
# ============================================================================
@app.route(route="api/gmail/unread", methods=["GET"], auth_level=func.AuthLevel.ANONYMOUS)
def get_gmail_unread(req: func.HttpRequest) -> func.HttpResponse:
    """
    Fetch unread emails from Gmail and moderate them.
    Automatically marks emails and applies labels based on moderation results.
    
    Query parameters:
    - max_results: number of emails to fetch (default 10)
    - auto_label: true/false - add 'FLAGGED' to violations (default true)
    - mark_read: true/false - mark as read after moderating (default false)
    
    Response: List of moderated emails with results
    """
    try:
        max_results = int(req.params.get("max_results", "10"))
        auto_label = req.params.get("auto_label", "true").lower() == "true"
        mark_read_after = req.params.get("mark_read", "false").lower() == "true"
        
        # Fetch unread emails from Gmail
        emails = fetch_unread_emails(max_results=max_results)
        
        if not emails:
            return func.HttpResponse(
                json.dumps({
                    "message": "No unread emails found",
                    "count": 0,
                    "emails": []
                }),
                mimetype="application/json"
            )
        
        moderated_emails = []
        current_time = datetime.datetime.now(datetime.timezone.utc).isoformat()
        
        for email in emails:
            # Moderate the email subject + body combined
            email_content = f"Subject: {email['subject']}\n\nBody: {email['body']}"
            moderation_result = check_message(email_content)
            
            moderation_result_full = {
                "gmail_id": email['gmail_id'],
                "from": email['from'],
                "subject": email['subject'],
                "body": email['body'][:500],  # Truncate for safety
                "is_allowed": moderation_result.get("is_allowed", True),
                "category": moderation_result.get("category", "clean"),
                "confidence": moderation_result.get("confidence", 1.0),
                "reason": moderation_result.get("reason", ""),
                "timestamp": current_time,
                "actions": []
            }
            
            # Handle violations
            if not moderation_result_full["is_allowed"]:
                category = moderation_result_full["category"]
                violation_stats[category] = violation_stats.get(category, 0) + 1
                
                # Add label if enabled
                if auto_label:
                    if add_label(email['gmail_id'], "FLAGGED"):
                        moderation_result_full["actions"].append("Added FLAGGED label")
                    
                    # Add category-specific labels
                    if add_label(email['gmail_id'], f"Violation_{category.upper()}"):
                        moderation_result_full["actions"].append(f"Added Violation_{category} label")
                
                # Send email alert if configured
                if should_send_alert(category, alert_config):
                    send_alert_email(
                        f"From: {email['from']}\nSubject: {email['subject']}\n\n{email['body'][:300]}",
                        category,
                        moderation_result_full["confidence"],
                        moderation_result_full["reason"]
                    )
                    moderation_result_full["actions"].append("Email alert sent")
            else:
                violation_stats["clean"] = violation_stats.get("clean", 0) + 1
            
            # Mark as read if enabled
            if mark_read_after:
                if mark_as_read(email['gmail_id']):
                    moderation_result_full["actions"].append("Marked as read")
            
            # Store in history
            message_history.append({
                "id": email['gmail_id'],
                "content": email_content[:1000],
                "username": email['from'],
                "is_allowed": moderation_result_full["is_allowed"],
                "category": moderation_result_full["category"],
                "confidence": moderation_result_full["confidence"],
                "reason": moderation_result_full["reason"],
                "timestamp": current_time,
                "source": "gmail"
            })
            
            moderated_emails.append(moderation_result_full)
        
        return func.HttpResponse(
            json.dumps({
                "count": len(moderated_emails),
                "emails": moderated_emails
            }),
            mimetype="application/json"
        )
    
    except Exception as e:
        logging.error(f"Error moderating Gmail emails: {e}")
        return func.HttpResponse(
            json.dumps({"error": str(e)}),
            status_code=400,
            mimetype="application/json"
        )


@app.route(route="api/gmail/stats", methods=["GET"], auth_level=func.AuthLevel.ANONYMOUS)
def get_gmail_stats(req: func.HttpRequest) -> func.HttpResponse:
    """Get Gmail mailbox statistics."""
    try:
        stats = get_email_stats()
        
        if not stats:
            return func.HttpResponse(
                json.dumps({
                    "error": "Gmail not configured or accessible",
                    "message": "Please set up Gmail OAuth credentials"
                }),
                status_code=503,
                mimetype="application/json"
            )
        
        return func.HttpResponse(
            json.dumps(stats),
            mimetype="application/json"
        )
    
    except Exception as e:
        logging.error(f"Error getting Gmail stats: {e}")
        return func.HttpResponse(
            json.dumps({"error": str(e)}),
            status_code=400,
            mimetype="application/json"
        )


# ============================================================================
# ORIGINAL MESSAGE CREATION ENDPOINT - For backward compatibility
# ============================================================================
@app.route(route="message", methods=["POST"], auth_level=func.AuthLevel.ANONYMOUS)
def create_message(req: func.HttpRequest) -> func.HttpResponse:
    logging.info('POST /message trigger function processed a request.')

    try:
        req_body = req.get_json()
    except ValueError:
        return func.HttpResponse(
             json.dumps({"error": "Invalid JSON in request body."}),
             status_code=400,
             mimetype="application/json"
        )

    if not isinstance(req_body, dict):
        return func.HttpResponse(
             json.dumps({"error": "Request body must be a JSON object."}),
             status_code=400,
             mimetype="application/json"
        )

    content = req_body.get('content')
    if content is None:
        return func.HttpResponse(
             json.dumps({"error": "'content' field is required."}),
             status_code=400,
             mimetype="application/json"
        )

    username = req_body.get('username')

    moderation_result = check_message(content)
    current_time = datetime.datetime.now(datetime.timezone.utc).isoformat()

    if not moderation_result.get("is_allowed", True):
        violations_container = get_container("violations")
        if violations_container:
            violation_record = {
                "id": str(uuid.uuid4()),
                "content": content,
                "username": username,
                "category": moderation_result.get("category"),
                "confidence": moderation_result.get("confidence"),
                "reason": moderation_result.get("reason"),
                "timestamp": current_time
            }
            try:
                violations_container.create_item(body=violation_record)
            except Exception as e:
                logging.error(f"Error creating violation item in Cosmos DB: {e}")
                
        return func.HttpResponse(
            json.dumps({
                "error": "Message blocked by moderation.",
                "category": moderation_result.get("category"),
                "reason": moderation_result.get("reason")
            }),
            status_code=403,
            mimetype="application/json"
        )

    new_message = {
        "id": str(uuid.uuid4()),
        "content": content,
        "username": username,
        "timestamp": current_time
    }
    
    container = get_container("messages")
    if container:
        try:
            container.create_item(body=new_message)
        except Exception as e:
            logging.error(f"Error creating item in Cosmos DB: {e}")
            return func.HttpResponse(
                 json.dumps({"error": "Failed to store message."}),
                 status_code=500,
                 mimetype="application/json"
            )
    else:
        logging.warning("Cosmos DB is not configured. Message was not stored.")

    return func.HttpResponse(
         json.dumps(new_message),
         status_code=200,
         mimetype="application/json"
    )

@app.route(route="messages", methods=["GET"], auth_level=func.AuthLevel.ANONYMOUS)
def get_messages(req: func.HttpRequest) -> func.HttpResponse:
    logging.info('GET /messages trigger function processed a request.')
    
    container = get_container()
    if not container:
        return func.HttpResponse(
            json.dumps([]),
            status_code=200,
            mimetype="application/json"
        )
    
    try:
        items = list(container.read_all_items())
        # Remove Cosmos DB metadata fields
        metadata_fields = ["_rid", "_self", "_etag", "_attachments", "_ts"]
        for item in items:
            for field in metadata_fields:
                item.pop(field, None)
                
        return func.HttpResponse(
            json.dumps(items),
            status_code=200,
            mimetype="application/json"
        )
    except Exception as e:
        logging.error(f"Error reading items from Cosmos DB: {e}")
        return func.HttpResponse(
            json.dumps({"error": "Failed to retrieve messages."}),
            status_code=500,
            mimetype="application/json"
        )

@app.route(route="health", methods=["GET"], auth_level=func.AuthLevel.ANONYMOUS)
def health_check(req: func.HttpRequest) -> func.HttpResponse:
    logging.info('GET /health trigger function processed a request.')
    
    return func.HttpResponse(
        "Healthy",
        status_code=200
    )