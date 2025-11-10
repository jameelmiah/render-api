import os, math, uuid, json
from typing import List
from fastapi import FastAPI, File, UploadFile, Form
from fastapi.responses import FileResponse, JSONResponse
from moviepy.editor import (
    VideoFileClip, ImageClip, CompositeVideoClip, TextClip,
    concatenate_videoclips, vfx, AudioFileClip
)

app = FastAPI()

# --- Config ---
TMP_DIR = "/tmp/renders"
FPS = 30
LEAD = 1.0
LAG  = 1.0

def ensure_dir(p: str):
    if not os.path.exists(p):
        os.makedirs(p, exist_ok=True)

def duration_for_text(txt: str, kind: str) -> float:
    cps = 14.0
    secs = max(len(txt)/cps, 7 if kind=="title" else 10)
    return float(min(secs, 8 if kind=="title" else 12))

def text_clip_bw(text: str, kind: str, w: int, h: int, dur: float) -> TextClip:
    # Use a font that exists in the container: DejaVuSans-Bold
    fontsize = 70 if kind == "title" else 58
    pad = 40
    txt = TextClip(
        text, method="caption", size=(w - 2*pad, None),
        align="West", color="black", font="DejaVu-Sans-Bold", fontsize=fontsize
    ).set_duration(dur)
    boxed = txt.on_color(
        size=(txt.w + pad*2, txt.h + pad*2),
        color="white", pos=("center","center"), col_opacity=1.0
    )
    SLIDE_IN_PX = 80
    def pos_at(t):
        enter = min(0.4, dur/4)
        exit_t = dur - min(0.4, dur/4)
        if t < enter:
            prog = t/enter
            x = int(-SLIDE_IN_PX * (1 - (1 - (1-prog)**2))) + 40
        elif t > exit_t:
            prog = (t - exit_t) / (dur - exit_t)
            x = int(40 - SLIDE_IN_PX * (prog**2))
        else:
            x = 40
        y = h - txt.h - 180
        return (x, y)
    return boxed.set_position(pos_at)

def load_media(path: str, w: int, h: int, need_dur: float):
    total = need_dur + LEAD + LAG
    ext = os.path.splitext(path)[1].lower()
    if ext in [".mp4",".mov",".mkv",".avi",".m4v"]:
        v = VideoFileClip(path).fx(vfx.resize, newsize=(w, None))
        if (v.duration or 0) >= total:
            v = v.subclip(0, total)
        else:
            loops = max(1, math.ceil(total / max(0.1, v.duration)))
            v = concatenate_videoclips([v] * loops).subclip(0, total)
        return v.resize((w, h)), True
    else:
        img = ImageClip(path).set_duration(total).fx(vfx.resize, newsize=(w, None))
        return img.resize((w, h)), False

def split_hard_160(text: str):
    MAX = 160
    rest = (text or "").strip()
    out = []
    while len(rest) > MAX:
        limit = MAX - 1
        slice_ = rest[:limit]
        cut = slice_.rfind(" ")
        if cut < 60: cut = limit
        out.append(slice_[:cut].strip() + "…")
        rest = rest[cut:].strip()
    out.append(rest)
    return out

def pick_assets(slides, media_paths):
    # Very simple: least-used first, reuse allowed
    use = {p: 0 for p in media_paths}
    for s in slides:
        if not media_paths:
            s["asset"] = None
            continue
        pick = sorted(media_paths, key=lambda p: use[p])[0]
        s["asset"] = pick
        use[pick] += 1
    return slides

@app.post("/render")
async def render(
    orientation: str = Form("vertical"),  # "vertical" or "landscape"
    slides_json: str = Form(None),        # preferred: JSON string of [{role,text,duration}]
    title: str = Form(""),
    intro: str = Form(""),
    body: str = Form("[]"),              # fallback path if slides_json not sent
    music: UploadFile | None = File(None),
    media: List[UploadFile] | None = File(None)  # pass multiple files as "media"
):
    job_id = str(uuid.uuid4())
    job_dir = os.path.join(TMP_DIR, job_id)
    ensure_dir(job_dir)
    out_path = os.path.join(job_dir, "final.mp4")

    # Frame size
    W,H = (1920,1080) if orientation == "landscape" else (1080,1920)

    # Save any media and music
    media_paths = []
    if media:
        if not isinstance(media, list): media = [media]
        for f in media:
            name = f.filename or f"media-{uuid.uuid4()}"
            p = os.path.join(job_dir, name)
            with open(p, "wb") as fp:
                fp.write(await f.read())
            media_paths.append(p)

    music_path = None
    if music:
        mp = os.path.join(job_dir, music.filename or "music.mp3")
        with open(mp, "wb") as fp:
            fp.write(await music.read())
        music_path = mp

    # Slides: prefer n8n's slides_json; else build a basic set from title/intro/body
    if slides_json:
        slides = json.loads(slides_json)
    else:
        body_items = json.loads(body) if body else []
        slides = []
        if title:
            slides.append({"role":"title","text":title,"duration":duration_for_text(title,"title")})
        for t in split_hard_160(intro):
            slides.append({"role":"body","text":t,"duration":duration_for_text(t,"body")})
        for item in body_items:
            for t in split_hard_160(item.get("text","")):
                slides.append({"role":"body","text":t,"duration":duration_for_text(t,"body")})

    # Assign assets
    slides = pick_assets(slides, media_paths)

    # Build video
    clips = []
    for s in slides:
        need = s["duration"]
        if s.get("asset"):
            bg, _ = load_media(s["asset"], W, H, need)
        else:
            bg = ImageClip(color=(255,255,255), size=(W,H)).set_duration(need+LEAD+LAG)
        txt = text_clip_bw(s["text"], s["role"], W, H, need)
        comp = CompositeVideoClip([bg.set_start(0), txt.set_start(LEAD)], size=(W,H)).subclip(0, need+LEAD+LAG)
        clips.append(comp.set_fps(FPS))

    video = concatenate_videoclips(clips, method="compose")

    if music_path and os.path.exists(music_path):
        try:
            audio = AudioFileClip(music_path)
            if audio.duration < video.duration:
                loops = max(1, math.ceil(video.duration / max(0.1, audio.duration)))
                audio = concatenate_videoclips([audio]*loops).subclip(0, video.duration)  # type: ignore
            else:
                audio = audio.subclip(0, video.duration)
            video = video.set_audio(audio.volumex(0.3))
        except Exception:
            pass

    video.write_videofile(out_path, fps=FPS, codec="libx264", audio_codec="aac", bitrate="6000k", verbose=False, logger=None)

    # Return a direct download (streamed) response
    return JSONResponse({"final_url": f"/download/{job_id}"})

@app.get("/download/{job_id}")
def download(job_id: str):
    p = os.path.join(TMP_DIR, job_id, "final.mp4")
    if not os.path.exists(p):
        return JSONResponse({"error":"not found"}, status_code=404)
    return FileResponse(path=p, media_type="video/mp4", filename="final.mp4")
