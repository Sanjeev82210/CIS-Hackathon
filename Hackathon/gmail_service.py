"""
Gmail Integration Service
Reads emails from Gmail inbox and prepares them for moderation.
Supports both Service Account and OAuth2 authentication methods.
"""

import os
import logging
import base64
import json
import pickle
from google.auth.transport.requests import Request
from google.oauth2.service_account import Credentials
from google.oauth2 import service_account
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError


# Gmail API scopes
SCOPES = ['https://www.googleapis.com/auth/gmail.readonly']


def get_gmail_service():
    """
    Creates a Gmail API service using either OAuth2 or Service Account.
    
    Priority order:
    1. Service Account (GMAIL_CREDENTIALS_JSON env var) - Best for automation
    2. OAuth2 (credentials.json file + token.pickle) - Best for user-specific access
    
    SETUP OPTIONS:
    
    Option 1 - Service Account (Recommended for automation):
    - Create service account in Google Cloud Console
    - Download JSON credentials file
    - Set GMAIL_CREDENTIALS_JSON env var with JSON content
    
    Option 2 - OAuth2 (For user-specific Gmail access):
    - Download credentials.json from Google Cloud Console (OAuth 2.0 Client ID)
    - Place credentials.json in project root
    - First run will open browser for auth
    - token.pickle created automatically
    """
    try:
        # Try service account first (better for automation)
        credentials_json = os.environ.get("GMAIL_CREDENTIALS_JSON")
        if credentials_json:
            try:
                credentials_info = json.loads(credentials_json)
                credentials = service_account.Credentials.from_service_account_info(
                    credentials_info,
                    scopes=['https://www.googleapis.com/auth/gmail.readonly']
                )
                service = build('gmail', 'v1', credentials=credentials)
                logging.info("Gmail service initialized with Service Account")
                return service
            except Exception as e:
                logging.warning(f"Service account auth failed: {e}. Trying OAuth2...")
        
        # Fall back to OAuth2 (user login)
        creds = None
        if os.path.exists('token.pickle'):
            try:
                with open('token.pickle', 'rb') as token:
                    creds = pickle.load(token)
                    logging.info("Loaded cached OAuth2 credentials from token.pickle")
            except Exception as e:
                logging.warning(f"Failed to load token.pickle: {e}")
        
        # Refresh or get new credentials
        if creds and creds.valid:
            service = build('gmail', 'v1', credentials=creds)
            logging.info("Gmail service initialized with OAuth2 (cached)")
            return service
        
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
                service = build('gmail', 'v1', credentials=creds)
                logging.info("Gmail service initialized with OAuth2 (refreshed)")
                return service
            except Exception as e:
                logging.warning(f"Failed to refresh OAuth2 token: {e}")
        
        # Need new OAuth2 authentication
        if os.path.exists('credentials.json'):
            logging.info("Starting OAuth2 authentication flow...")
            flow = InstalledAppFlow.from_client_secrets_file(
                'credentials.json', SCOPES)
            creds = flow.run_local_server(port=0)
            
            # Save credentials for next run
            try:
                with open('token.pickle', 'wb') as token:
                    pickle.dump(creds, token)
                logging.info("OAuth2 credentials saved to token.pickle")
            except Exception as e:
                logging.warning(f"Failed to save token.pickle: {e}")
            
            service = build('gmail', 'v1', credentials=creds)
            logging.info("Gmail service initialized with OAuth2 (new auth)")
            return service
        
        # No credentials available
        logging.warning("No Gmail credentials found (no service account env var or credentials.json file)")
        return None
        
    except Exception as e:
        logging.error(f"Failed to create Gmail service: {e}")
        return None


def fetch_unread_emails(max_results=10):
    """
    Fetch unread emails from Gmail inbox.
    
    Returns list of emails with:
    - id: Gmail message ID
    - subject: Email subject
    - from: Sender email
    - body: Email body/content
    - date: Email date
    """
    service = get_gmail_service()
    if not service:
        logging.warning("Gmail service not configured or accessible")
        return []
    
    try:
        # Query for unread messages
        results = service.users().messages().list(
            userId='me',
            q='is:unread',
            maxResults=max_results
        ).execute()
        
        messages = results.get('messages', [])
        emails = []
        
        for message in messages:
            try:
                msg_data = service.users().messages().get(
                    userId='me',
                    id=message['id'],
                    format='full'
                ).execute()
                
                headers = msg_data['payload']['headers']
                subject = next((h['value'] for h in headers if h['name'] == 'Subject'), 'No Subject')
                from_addr = next((h['value'] for h in headers if h['name'] == 'From'), 'Unknown')
                date = next((h['value'] for h in headers if h['name'] == 'Date'), '')
                
                # Extract body
                body = extract_body(msg_data['payload'])
                
                emails.append({
                    'id': message['id'],
                    'subject': subject,
                    'from': from_addr,
                    'body': body,
                    'date': date,
                    'gmail_id': message['id']
                })
                
            except HttpError as e:
                logging.error(f"Error fetching message {message['id']}: {e}")
                continue
        
        return emails
    
    except HttpError as error:
        logging.error(f'An error occurred while fetching emails: {error}')
        return []


def extract_body(payload):
    """Extract email body from Gmail payload."""
    try:
        if 'parts' in payload:
            # Multipart message
            parts = payload['parts']
            data = ''
            for part in parts:
                if part['mimeType'] == 'text/plain':
                    if 'data' in part['body']:
                        data = base64.urlsafe_b64decode(part['body']['data']).decode('utf-8')
                        break
            return data
        else:
            # Simple message
            if 'body' in payload and 'data' in payload['body']:
                return base64.urlsafe_b64decode(payload['body']['data']).decode('utf-8')
    except Exception as e:
        logging.error(f"Error extracting email body: {e}")
    
    return ""


def mark_as_read(message_id):
    """Mark an email as read in Gmail."""
    service = get_gmail_service()
    if not service:
        return False
    
    try:
        service.users().messages().modify(
            userId='me',
            id=message_id,
            body={'removeLabelIds': ['UNREAD']}
        ).execute()
        return True
    except HttpError as error:
        logging.error(f'Error marking message as read: {error}')
        return False


def add_label(message_id, label_name):
    """Add a label to an email (e.g., 'FLAGGED', 'IMPORTANT')."""
    service = get_gmail_service()
    if not service:
        return False
    
    try:
        # Get label ID by name
        results = service.users().labels().list(userId='me').execute()
        labels = results.get('labels', [])
        label_id = next((l['id'] for l in labels if l['name'] == label_name), None)
        
        if not label_id:
            # Create label if it doesn't exist
            label_object = {
                'name': label_name,
                'labelListVisibility': 'labelShow',
                'messageListVisibility': 'show'
            }
            created_label = service.users().labels().create(
                userId='me',
                body=label_object
            ).execute()
            label_id = created_label['id']
        
        service.users().messages().modify(
            userId='me',
            id=message_id,
            body={'addLabelIds': [label_id]}
        ).execute()
        return True
    except HttpError as error:
        logging.error(f'Error adding label: {error}')
        return False


def get_email_stats():
    """Get Gmail mailbox statistics."""
    service = get_gmail_service()
    if not service:
        return None
    
    try:
        profile = service.users().getProfile(userId='me').execute()
        return {
            'total_messages': profile.get('messagesTotal', 0),
            'unread_messages': profile.get('messagesUnread', 0),
            'threads_total': profile.get('threadsTotal', 0)
        }
    except HttpError as error:
        logging.error(f'Error getting Gmail stats: {error}')
        return None


def fetch_unread_emails(max_results=10):
    """
    Fetch unread emails from Gmail inbox.
    
    Returns list of emails with:
    - id: Gmail message ID
    - subject: Email subject
    - from: Sender email
    - body: Email body/content
    - date: Email date
    """
    service = get_gmail_service()
    if not service:
        logging.warning("Gmail service not configured")
        return []
    
    try:
        # Query for unread messages
        results = service.users().messages().list(
            userId='me',
            q='is:unread',
            maxResults=max_results
        ).execute()
        
        messages = results.get('messages', [])
        emails = []
        
        for message in messages:
            try:
                msg_data = service.users().messages().get(
                    userId='me',
                    id=message['id'],
                    format='full'
                ).execute()
                
                headers = msg_data['payload']['headers']
                subject = next((h['value'] for h in headers if h['name'] == 'Subject'), 'No Subject')
                from_addr = next((h['value'] for h in headers if h['name'] == 'From'), 'Unknown')
                date = next((h['value'] for h in headers if h['name'] == 'Date'), '')
                
                # Extract body
                body = extract_body(msg_data['payload'])
                
                emails.append({
                    'id': message['id'],
                    'subject': subject,
                    'from': from_addr,
                    'body': body,
                    'date': date,
                    'gmail_id': message['id']
                })
                
            except HttpError as e:
                logging.error(f"Error fetching message {message['id']}: {e}")
                continue
        
        return emails
    
    except HttpError as error:
        logging.error(f'An error occurred while fetching emails: {error}')
        return []


def extract_body(payload):
    """Extract email body from Gmail payload."""
    try:
        if 'parts' in payload:
            # Multipart message
            parts = payload['parts']
            data = ''
            for part in parts:
                if part['mimeType'] == 'text/plain':
                    if 'data' in part['body']:
                        data = base64.urlsafe_b64decode(part['body']['data']).decode('utf-8')
                        break
            return data
        else:
            # Simple message
            if 'body' in payload and 'data' in payload['body']:
                return base64.urlsafe_b64decode(payload['body']['data']).decode('utf-8')
    except Exception as e:
        logging.error(f"Error extracting email body: {e}")
    
    return ""


def mark_as_read(message_id):
    """Mark an email as read in Gmail."""
    service = get_gmail_service()
    if not service:
        return False
    
    try:
        service.users().messages().modify(
            userId='me',
            id=message_id,
            body={'removeLabelIds': ['UNREAD']}
        ).execute()
        return True
    except HttpError as error:
        logging.error(f'Error marking message as read: {error}')
        return False


def add_label(message_id, label_name):
    """Add a label to an email (e.g., 'FLAGGED', 'IMPORTANT')."""
    service = get_gmail_service()
    if not service:
        return False
    
    try:
        # Get label ID by name
        results = service.users().labels().list(userId='me').execute()
        labels = results.get('labels', [])
        label_id = next((l['id'] for l in labels if l['name'] == label_name), None)
        
        if not label_id:
            # Create label if it doesn't exist
            label_object = {
                'name': label_name,
                'labelListVisibility': 'labelShow',
                'messageListVisibility': 'show'
            }
            created_label = service.users().labels().create(
                userId='me',
                body=label_object
            ).execute()
            label_id = created_label['id']
        
        service.users().messages().modify(
            userId='me',
            id=message_id,
            body={'addLabelIds': [label_id]}
        ).execute()
        return True
    except HttpError as error:
        logging.error(f'Error adding label: {error}')
        return False


def get_email_stats():
    """Get Gmail mailbox statistics."""
    service = get_gmail_service()
    if not service:
        return None
    
    try:
        profile = service.users().getProfile(userId='me').execute()
        return {
            'total_messages': profile.get('messagesTotal', 0),
            'unread_messages': profile.get('messagesUnread', 0),
            'threads_total': profile.get('threadsTotal', 0)
        }
    except HttpError as error:
        logging.error(f'Error getting Gmail stats: {error}')
        return None
