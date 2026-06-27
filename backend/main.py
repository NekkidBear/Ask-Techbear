"""
main.py — FastAPI application entry point for Ask TechBear
Gymnarctos Studios LLC
"""

from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.database import init_db
from backend.routers import questions

load_dotenv()

# =============================================================
# Lifespan — runs on startup and shutdown
# =============================================================


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """
    Startup: initialize database tables.
    Shutdown: clean up connections.
    """
    # Startup
    await init_db()
    print("✅ Database initialized")
    print("✅ Ask TechBear API is ready")
    yield
    # Shutdown
    print("👋 Ask TechBear shutting down")


# =============================================================
# App initialization
# =============================================================

app = FastAPI(
    title="Ask TechBear",
    description="Live Q&A performance tool for Gymnarctos Studios tabling events",
    version="0.1.0",
    lifespan=lifespan,
)

# =============================================================
# CORS — controls which origins can call the API
# The React frontend (port 3000) needs to be allowed.
# The moderator dashboard is localhost only — never expose
# /dashboard through the Cloudflare tunnel.
# In production (tunnel), only the public submission URL is allowed.
# =============================================================

ALLOWED_ORIGINS = [
    "http://localhost:3000",        # React dev server
    "http://localhost:5173",        # Vite default port
    "https://ask-techbear.gymnarctosstudiosllc.com",  # Public tunnel URL
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# =============================================================
# Routers
# =============================================================

app.include_router(
    questions.router, prefix="/api/questions", tags=["questions"])


# =============================================================
# Health check — confirms API is running
# =============================================================

@app.get("/health")
async def health_check():
    """
    Simple health check endpoint.
    Hit this to confirm the API is up before an event.
    """
    return {
        "status": "ok",
        "message": "TechBear is in the building, sugar! 🐻",
        "version": "0.1.0",
    }


@app.get("/")
async def root():
    """Root endpoint — redirects to health check info."""
    return {
        "message": "Ask TechBear API",
        "docs": "/docs",
        "health": "/health",
    }
