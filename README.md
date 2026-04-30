# youtube-album-tools

把 YouTube 上的专辑/mix 扒下来，按曲目切成带标签的 MP3，封面塞进文件里。元数据走 Discogs。

## 能干的事

- 最高音质下 YouTube 音频（yt-dlp）
- 用 YouTube 章节/简介时间戳/Discogs 曲目时长切轨
- 从 Discogs 拉专辑信息、曲目列表、封面
- 输出带封面的 320kbps MP3，元数据写进标签
- 可以批处理（给一个 txt 文件扔一堆链接）
- 跑完后能验证输出，缺标签的可以修

## 前置

- Python 3.9+
- yt-dlp
- ffmpeg
- Discogs token

装依赖：

```bash
pip install yt-dlp
brew install ffmpeg   # macOS
```

Discogs token 放环境变量 `DISCOGS_TOKEN`，脚本启动时自己会读。

## 跑起来

单张专辑：

```bash
python3 scripts/process_youtube_album.py "https://www.youtube.com/watch?v=xxxxx"
```

输出默认在 `/Users/haoxiangliu/albums`，改路径：

```bash
python3 scripts/process_youtube_album.py "https://www.youtube.com/watch?v=xxxxx" --output-root /你的路径
```

批处理：

```bash
python3 scripts/batch_process_albums.py urls.txt
```

验证输出：

```bash
python3 scripts/validate_album_outputs.py --output-root ./output
python3 scripts/validate_album_outputs.py --fix ./output/某专辑
```

## 怎么切的

切轨顺序：

1. 先看 YouTube 有没有章节标记
2. 再看简介里有没有时间戳
3. 都没有的话用 Discogs 曲目时长累加

Discogs 匹配不走上传者信息（因为很多视频是别人重传的），只从标题和简介里提艺术家/专辑名去搜。

## 文件

scripts/process_youtube_album.py — 主脚本，下载+切轨+标签一条龙
scripts/batch_process_albums.py — 批量跑
scripts/validate_album_outputs.py — 查输出，能修标缺失的
references/metadata-strategy.md — Discogs 匹配和回退策略
SKILL.md — Codex skill 定义
agents/openai.yaml — skill 元数据

## 输出

跑完后每个专辑一个文件夹，里面有切好的 MP3，以及：

- `manifest.json` — 每轨文件名、时长、标签
- `release.json` — 专辑信息、Discogs 链接

Discogs 搜不到的话 `release.json` 里会标 `discogs_found=false`，曲目照切，只是标签不全。
