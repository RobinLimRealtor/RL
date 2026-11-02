# Auto Video Editor

Upload a raw video, type what you want in plain words, and get back a finished video that's ready to post.

It handles the boring parts automatically:

- **Cuts dead space**: removes long pauses and "um"/"uh" filler words, and (in smart mode) repeated takes.
- **Captions**: burned into the video in one of three styles. You also get a `.srt` caption file for YouTube.
- **B-roll**: drops cutaway clips over your voice, using your own clips first and free stock footage when you don't have a match.
- **Music**: background music picked by mood, which dips automatically while you're talking.
- **Sound effects**: whooshes when b-roll comes in, plus extra sounds when you ask for them.
- **Shapes**: vertical 9:16 for Reels, TikTok and Shorts, 16:9 for YouTube, square, or the original shape.
- **Follow-up changes**: type "less music" or "cut the first 5 seconds" and get a new version. Earlier versions are kept.

It runs on your own computer (Windows or Mac). Your videos are never uploaded anywhere.

---

## 1. One-time setup

### Windows

1. Install **Python** from <https://www.python.org/downloads/>. On the first install screen, **tick "Add python.exe to PATH"**, then click Install.
2. Download this project: on the GitHub page click the green **Code** button, then **Download ZIP**. Unzip it somewhere easy, like your Desktop.
3. Open the folder and double-click **`start-windows.bat`**.
   - If Windows shows "Windows protected your PC", click **More info**, then **Run anyway**.
   - The first start takes a few minutes while it installs what it needs. After that it starts in seconds.

### Mac

1. Install **Python** from <https://www.python.org/downloads/> (the normal installer).
2. Download this project: on the GitHub page click the green **Code** button, then **Download ZIP**. Unzip it somewhere easy, like your Desktop.
3. Open the folder, **right-click `start-mac.command`, choose Open**, and confirm with **Open**. You only need to right-click the first time. After that a double-click works.
   - The first start takes a few minutes while it installs what it needs.

When it's ready, your browser opens the editor. **Keep the black window open while you use it.** Closing that window stops the app.

> The first video you edit also downloads the speech-recognition model (about 500 MB, one time only).

---

## 2. Editing a video

1. Click **+ New video**, drop in your video, and type what you want. For example:
   - *Tighten the pauses, bold captions, upbeat music*
   - *Vertical for Reels, captions, b-roll over the kitchen and backyard parts, whoosh on each b-roll*
   - *Home tour: calm luxury music, no captions*
   - *YouTube 16:9, clean captions, cut the part where I say "let me start again"*
2. Click **Make my video**. You'll see what it's doing step by step.
3. Watch the result, read **What I did**, and click **Download video**.
4. Want changes? Type them in **Want changes?** and click **Update video**. For example: *less music*, *no captions*, *make it tighter*, *more b-roll*, *switch to calm music*. Each change makes a new version, and you can flip between versions.

---

## 3. Smart mode (recommended)

Without any setup the editor runs in **simple mode**. It cuts pauses, adds captions and music, and understands simple keywords like "vertical", "no captions" or "less music".

Add a **Claude API key** and it understands any request in your own words. It also finds bad takes, picks the best moments for b-roll, and looks at your b-roll clips to know what's in them.

1. Go to <https://console.anthropic.com/settings/keys>, sign up, add a little credit, and create a key.
2. In the editor click **Settings**, paste the key under **Smart editing**, then click **Save**.

Cost is usually a few cents per video, and the first scan of a big b-roll folder costs a bit more because it looks at every clip once.

### Free stock b-roll

For stock footage when your own clips don't fit, get a free key at <https://www.pexels.com/api/> and paste it under **Stock b-roll** in Settings.

---

## 4. Your b-roll and music

- **B-roll:** put your clips (house exteriors, rooms, drone shots, you walking in, signs, and so on) in the **`my-broll`** folder inside this project. You can also use **Settings → Add clips** or point Settings at any folder you already have. Clear file names help in simple mode, for example `kitchen-quartz-island.mp4`.
- **Music:** the app comes with two simple built-in tracks so music works right away. For better music, add royalty-free songs (for example from the YouTube Audio Library) in **Settings → Music**, choosing a mood for each. They're stored in `library/music/<mood>/`.
- **Sound effects:** built-in `whoosh`, `swoosh`, `pop` and `ding` live in `library/sfx/`. Add your own sounds there (for example `cash-register.wav`) and ask for them by name.

---

## 5. Where things are saved

- Edits, versions and settings: the `workspace` folder inside this project. Each video has its own folder with the edited MP4 and caption file.
- Your API keys are saved only on your computer (`workspace/settings.json`). They never go to GitHub.

---

## 6. If something goes wrong

- **"Python … was not found"**: install Python (step 1), then start again. On Windows make sure "Add python.exe to PATH" was ticked. If not, run the installer again, choose **Modify**, and tick it.
- **Setup failed**: check your internet connection and start again. Still failing? Delete the `.venv` folder in this project and start again. On a Mac, `.venv` is hidden, so press Cmd+Shift+. in Finder to show it.
- **Mac says the file can't be opened**: right-click `start-mac.command` and choose **Open**. If that doesn't work, open the Terminal app, type `bash ` (with a space), drag `start-mac.command` into the window, and press Enter.
- **An edit shows "Needs attention"**: the message underneath says what went wrong. Type your request again to retry.
- **Slow?** Longer videos take a few minutes. In Settings you can choose **Fast** speech recognition.

---

## For developers

Python 3.10+, with ffmpeg bundled through `imageio-ffmpeg`.

```
autoedit/
  __main__.py     start the local web app (python -m autoedit)
  server.py       Flask routes (served by waitress on 127.0.0.1)
  jobs.py         background pipeline: probe → transcribe → plan → fetch b-roll → render
  transcribe.py   faster-whisper with word timestamps
  planner.py      Claude edit plan (structured JSON) + keyword fallback ("simple mode")
  cutting.py      dead-space removal and original→edited time mapping
  captions.py     .ass (burned in, word highlight) and .srt
  render.py       ffmpeg: cut segments, concat, overlay b-roll, captions, music ducking, SFX, loudness
  library.py      own b-roll scan and AI tagging, music/SFX library, built-in synthesized audio
  stock.py        Pexels stock video search/download
  static/         the web page
```

Run the tests with `pip install pytest && python -m pytest -q`. They render a real video end to end with a fixed transcript, so no network is needed.

The edit plan uses times on the original video's timeline. `cutting.TimeMap` converts them to the edited timeline after pauses are removed. Planning uses `claude-opus-5-5` by default, which you can change with `claude_model` in `workspace/settings.json`.
