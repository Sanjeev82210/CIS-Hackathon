"""
Email alert service for moderation violations.
Sends email notifications when flagged messages are detected.
"""

import smtplib
import os
import logging
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart


def send_alert_email(message_content: str, category: str, confidence: float, reason: str) -> bool:
    """
    Sends an email alert for a moderation violation.
    Returns True if email sent successfully, False otherwise.
    Uses fail-open pattern: logs error but doesn't break the app.
    """
    try:
        # Configuration from environment variables
        # CONFIGURE THESE IN local.settings.json:
        smtp_server = os.environ.get("ALERT_SMTP_SERVER", "smtp.gmail.com")
        smtp_port = int(os.environ.get("ALERT_SMTP_PORT", 587))
        sender_email = os.environ.get("ALERT_EMAIL_FROM", "")
        sender_password = os.environ.get("ALERT_EMAIL_PASS", "")
        recipient_email = os.environ.get("ALERT_EMAIL_TO", "")

        if not all([sender_email, sender_password, recipient_email]):
            logging.warning("Email alert disabled: Missing email configuration in environment variables.")
            return False

        # Create email message
        msg = MIMEMultipart()
        msg["From"] = sender_email
        msg["To"] = recipient_email
        msg["Subject"] = f"🚨 Moderation Alert: {category.upper()}"

        body = f"""
Moderation Violation Detected

Message: {message_content}

Category: {category.upper()}
Confidence: {confidence:.0%}
Reason: {reason}

---
AI Moderation System
        """.strip()

        msg.attach(MIMEText(body, "plain"))

        # Send email
        with smtplib.SMTP(smtp_server, smtp_port) as server:
            server.starttls()
            server.login(sender_email, sender_password)
            server.send_message(msg)

        logging.info(f"Email alert sent to {recipient_email} for {category} violation")
        return True

    except Exception as e:
        logging.error(f"Failed to send email alert: {e}")
        return False


def should_send_alert(category: str, alert_config: dict) -> bool:
    """
    Determines if an alert should be sent based on the alert configuration.
    
    alert_config example:
    {
        "enabled": true,
        "trigger_level": "all"  # "all", "toxic_harassment", "only_toxic", "only_harassment", "only_spam"
    }
    """
    if not alert_config.get("enabled", False):
        return False

    trigger_level = alert_config.get("trigger_level", "all")

    if trigger_level == "all":
        return True
    elif trigger_level == "toxic_harassment":
        return category in ["toxic", "harassment"]
    elif trigger_level == "only_toxic":
        return category == "toxic"
    elif trigger_level == "only_harassment":
        return category == "harassment"
    elif trigger_level == "only_spam":
        return category == "spam"

    return False
