from fastapi import FastAPI
import http

app = FastAPI()

@app.get("/health")
def get_health():
    return {"status": "ok"}
