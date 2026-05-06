# Copilot Instructions

## Project Overview

This is an **AI Chat Moderation** system built for a CIS Hackathon. It consists of:

- **Backend**: Python Azure Functions (v4) that expose a REST API for sending and retrieving chat messages, with AI-powered content moderation via the Gemini API.
- **Frontend**: A single-page HTML dashboard (`frontend/dashboard.html`) for viewing messages and moderation statistics.

## Repository Structure

```
CIS-Hackathon/
├── Backend/
│   ├── function_app.py       # Azure Functions entry point (POST /message, GET /messages, GET /health)
│   ├── moderation.py         # Gemini API integration for content moderation
│   ├── requirements.txt      # Python dependencies (azure-functions, azure-cosmos, requests)
│   └── host.json             # Azure Functions host configuration
└── frontend/
    └── dashboard.html        # Single-page moderation dashboard
```

## Key Technologies

- **Runtime**: Python 3.11, Azure Functions v4
- **Database**: Azure Cosmos DB (`streamingdb` database, `messages` and `violations` containers)
- **AI Moderation**: Google Gemini 1.5 Flash (`GEMINI_API_KEY` environment variable required)
- **Frontend**: Vanilla HTML/CSS/JavaScript (no build step)

## Environment Variables

| Variable                   | Description                                      |
| -------------------------- | ------------------------------------------------ |
| `COSMOS_CONNECTION_STRING` | Azure Cosmos DB connection string                |
| `GEMINI_API_KEY`           | Google Gemini API key for content moderation     |

## API Endpoints

| Method | Route       | Description                               |
| ------ | ----------- | ----------------------------------------- |
| POST   | `/message`  | Submit a new chat message (with moderation) |
| GET    | `/messages` | Retrieve all stored messages              |
| GET    | `/health`   | Health check                              |

## Coding Conventions

- Follow PEP 8 for Python code.
- Use `logging` (not `print`) for diagnostics in the backend.
- The moderation check uses a **fail-open** pattern: if the Gemini API is unavailable, messages are allowed through.
- Blocked messages are stored in the `violations` Cosmos DB container with their category and reason.
- Strip Cosmos DB metadata fields (`_rid`, `_self`, `_etag`, `_attachments`, `_ts`) from API responses.
