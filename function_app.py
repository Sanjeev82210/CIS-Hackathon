import azure.functions as func
import datetime
import json
import logging
import uuid
import os
from azure.cosmos import CosmosClient
from moderation import check_message

app = func.FunctionApp()

def get_container(container_name="messages"):
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