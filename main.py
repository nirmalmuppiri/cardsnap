import asyncio
import mimetypes
import os
import uuid
import urllib.parse
from pathlib import Path

from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from dotenv import load_dotenv

load_dotenv()

import store
import extract as extractor
import excel as excel_gen
import storage

store.init_db()

UPLOAD_DIR = store.DB_PATH.parent / "uploads"
UPLOAD_DIR.mkdir(exist_ok=True)

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
    event_name: str = Form(...),
    exhibitor_name: str = Form(...),
    images: list[UploadFile] = File(...),
):
    async def process_one(img: UploadFile) -> dict:
        raw = await img.read()
        mime = img.content_type or mimetypes.guess_type(img.filename or "")[0] or "image/jpeg"
        ext = mime.split("/")[-1].split(";")[0] or "jpg"

        image_path = str(UPLOAD_DIR / f"{uuid.uuid4()}.{ext}")
        Path(image_path).write_bytes(raw)

        # Upload to R2 if configured (non-blocking best-effort)
        r2_key = storage.upload(image_path, event_name, exhibitor_name)

        upload_id = store.save_upload(event_name, exhibitor_name, image_path, r2_key)

        try:
            data = extractor.extract_card(raw, mime_type=mime)
            status = "ok"
        except Exception as e:
            data = {"error": str(e)}
            status = "error"

        return {"filename": img.filename, "upload_id": upload_id, "data": data, "status": status}

    results = await asyncio.gather(*[process_one(img) for img in images])
    return JSONResponse({"event_name": event_name, "cards": results})


@app.post("/uploads/{upload_id}/retry")
async def retry_upload(upload_id: int):
    upload = store.get_upload(upload_id)
    if not upload:
        return JSONResponse({"error": "Upload not found"}, status_code=404)

    image_path = Path(upload["image_path"])
    if not image_path.exists():
        return JSONResponse({"error": "Image file missing"}, status_code=404)

    raw = image_path.read_bytes()
    suffix = image_path.suffix.lstrip(".")
    mime = f"image/{suffix}" if suffix else "image/jpeg"

    try:
        data = extractor.extract_card(raw, mime_type=mime)
        return JSONResponse({"data": data, "status": "ok"})
    except Exception as e:
        return JSONResponse({"data": {"error": str(e)}, "status": "error"})


@app.get("/uploads/{upload_id}/image")
async def serve_upload(upload_id: int):
    upload = store.get_upload(upload_id)
    if not upload:
        return Response(status_code=404)

    image_path = Path(upload["image_path"])
    if not image_path.exists():
        return Response(status_code=404)

    suffix = image_path.suffix.lstrip(".")
    mime = f"image/{suffix}" if suffix else "image/jpeg"
    return Response(content=image_path.read_bytes(), media_type=mime)


@app.post("/contacts")
async def save_contacts(request: Request):
    body = await request.json()
    event_name = body["event_name"]
    cards = body["cards"]
    ids = []
    for card in cards:
        cid = store.save_contact(event_name, card["data"])
        if card.get("upload_id"):
            store.link_upload_to_contact(card["upload_id"], cid)
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


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8080))
    uvicorn.run(app, host="0.0.0.0", port=port)
