import asyncio
import mimetypes
import urllib.parse
from pathlib import Path

from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from dotenv import load_dotenv

load_dotenv()

import store
import extract as extractor
import excel as excel_gen

store.init_db()

app = FastAPI()
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")


@app.get("/", response_class=HTMLResponse)
async def index(request: Request, event: str = ""):
    events = store.list_events()
    contacts = store.get_contacts(event or None)
    return templates.TemplateResponse("index.html", {
        "request": request,
        "contacts": contacts,
        "events": events,
        "current_event": event,
    })


@app.post("/scan")
async def scan(
    request: Request,
    event_name: str = Form(...),
    images: list[UploadFile] = File(...),
):
    """
    Receive multiple images, extract all cards in parallel,
    return a JSON list of {filename, data} for the review form.
    """
    async def process_one(img: UploadFile) -> dict:
        raw = await img.read()
        mime = img.content_type or mimetypes.guess_type(img.filename or "")[0] or "image/jpeg"
        try:
            data = extractor.extract_card(raw, mime_type=mime)
        except Exception as e:
            data = {"error": str(e)}
        return {"filename": img.filename, "data": data}

    results = await asyncio.gather(*[process_one(img) for img in images])
    return JSONResponse({"event_name": event_name, "cards": results})


@app.post("/contacts")
async def save_contacts(request: Request):
    """Save a batch of confirmed contacts."""
    body = await request.json()
    event_name = body["event_name"]
    cards = body["cards"]  # list of {data: {...}}
    ids = []
    for card in cards:
        cid = store.save_contact(event_name, card["data"])
        ids.append(cid)
    return JSONResponse({"saved": len(ids)})


@app.put("/contacts/{contact_id}")
async def update_contact(contact_id: int, request: Request):
    body = await request.json()
    store.update_contact(contact_id, body["data"])
    return JSONResponse({"ok": True})


@app.delete("/contacts/{contact_id}")
async def delete_contact(contact_id: int):
    store.delete_contact(contact_id)
    return JSONResponse({"ok": True})


@app.get("/export")
async def export(event: str = ""):
    contacts = store.get_contacts(event or None)
    if not contacts:
        return JSONResponse({"error": "No contacts found"}, status_code=404)

    label = event or "all_contacts"
    xlsx_bytes = excel_gen.generate_excel(contacts, label)
    filename = urllib.parse.quote(label.replace(" ", "_")) + ".xlsx"

    return Response(
        content=xlsx_bytes,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
