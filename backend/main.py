from datetime import datetime, timezone

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from config import settings

app = FastAPI(
    title=settings.APP_NAME,
    description="High-concurrency event ticketing engine",
    version="0.1.0",
)

# CORS: frontend 5173 pe hai, backend 8000 pe. Browser inhe alag websites
# maanta hai, isliye explicitly allow karna padta hai.
# Ab origins hardcoded nahi hain — config se aa rahe hain.
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
def read_root():
    return {"message": "FastAPI Server Running Perfectly!"}


@app.get("/api/health")
def health_check():
    """Health check — frontend isi endpoint se backend ka status check karta hai."""
    return {
        "status": "healthy",
        "service": settings.APP_NAME,
        "version": "0.1.0",
        "time": datetime.now(timezone.utc).isoformat(),
    }
