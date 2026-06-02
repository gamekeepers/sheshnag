import os
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from database import engine, Base
from routers import files, batches, workers

Base.metadata.create_all(bind=engine)

app = FastAPI(title="Batch AI Compute Platform")

app.add_middleware(
    CORSMiddleware,
    allow_origins=os.environ.get("CORS_ORIGINS", "http://localhost:3000").split(","),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(files.router,   prefix="/v1",     tags=["Files"])
app.include_router(batches.router, prefix="/v1",     tags=["Batches"])
app.include_router(workers.router, prefix="/workers", tags=["Workers"])

@app.get("/")
def health():
    return {"status": "ok"}
