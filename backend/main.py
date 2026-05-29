from fastapi import FastAPI
from database import engine, Base
from routers import jobs, workers

Base.metadata.create_all(bind=engine)

app = FastAPI(title="Batch AI Compute Platform")

app.include_router(jobs.router,    prefix="/jobs",    tags=["Jobs"])
app.include_router(workers.router, prefix="/workers", tags=["Workers"])

@app.get("/")
def health():
    return {"status": "ok"}