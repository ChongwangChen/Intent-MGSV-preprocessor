#!/usr/bin/env python3
"""
music_fetch.py — 自动识曲 + 下载完整原曲（v2）
依赖: pip install shazamio yt-dlp requests librosa

v2 新增:
  1. 多窗口投票识曲: 音乐段切 2~3 个窗口分别 Shazam，一致才采信
  2. ACRCloud 兜底: Shazam 失败/冲突时换引擎（需 acrcloud_config.json）
  3. 自动 QQ 搜索: 识别结果按 标题+歌手相似度 选最佳 song_mid 下载
  4. 指纹校验: 下载后用 chroma 互相关验证是否下对歌，并精算 song_offset

新列:
  recog_confidence  vote-3/3 / vote-2/3 / vote-2/2 / single / acrcloud / conflict
  recog_note        冲突候选、低匹配警告等备注
  match_score       chroma 互相关得分（≥0.35 好, 0.15~0.35 存疑, <0.15 可能下错）
"""

import asyncio, os, re, glob, json, subprocess, sys, tempfile, shutil
import base64, hashlib, hmac, time as _time
import wave
from difflib import SequenceMatcher
import pandas as pd
import requests
import warnings
warnings.filterwarnings('ignore', message='PySoundFile failed')
warnings.filterwarnings('ignore', category=FutureWarning)

EXCEL          = r"E:\MGSV_preprocessor\outputs\MGSV_Master_Dataset.xlsx"
OUT_DIR        = r"E:\MGSV_preprocessor\outputs\full_songs"
SCRIPT_DIR     = os.path.dirname(os.path.abspath(__file__))
MAX_DURATION   = 600  # 秒，超过则丢弃
COOKIES_FILE   = os.path.join(SCRIPT_DIR, "qqmusic_cookies.txt")
BILI_COOKIES_FILE = os.path.join(SCRIPT_DIR, "bilibili_cookies.txt")
ACR_CONFIG     = os.path.join(SCRIPT_DIR, "acrcloud_config.json")
ARIA2C_EXE     = r"E:\tools\aria2-1.37.0-win-64bit-build1\aria2c.exe"
FFMPEG_EXE     = r"E:\Users\30993\miniconda3\envs\mgsv_data\Library\bin\ffmpeg.exe"
FFPROBE_EXE    = r"E:\Users\30993\miniconda3\envs\mgsv_data\Library\bin\ffprobe.exe"

WIN_LEN        = 10.0   # 识别窗口长度（秒）
MATCH_GOOD     = 0.45   # chroma 校验阈值（去均值相关，无关内容≈0）
MATCH_MIN      = 0.30
DL_TIMEOUT     = 20    # 单来源单次下载硬超时（秒），超时杀进程换源
SOURCES        = ('qq', 'yt', 'bili')   # 下载源顺序；代理烂时可删掉 'yt'
YT_PROXY       = "socks5://127.0.0.1:33210"   # YouTube + ACRCloud 共用（QQ/B站直连）；socks 需 pip install requests[socks]

os.makedirs(OUT_DIR, exist_ok=True)

def _aria2c_path():
    if os.path.exists(ARIA2C_EXE):
        return ARIA2C_EXE
    return shutil.which('aria2c')

if os.path.exists(COOKIES_FILE):
    print(f"✓ QQ Cookie 文件找到: {COOKIES_FILE}")
else:
    print(f"✗ QQ Cookie 文件未找到: {COOKIES_FILE}")

# ── 工具 ─────────────────────────────────────────────────────────────────
def resolve_path(fmp: str):
    if not fmp or str(fmp).strip() in ('', 'nan'): return None
    fmp = fmp.replace('\\', os.sep)
    if os.path.isabs(fmp): return fmp if os.path.exists(fmp) else None
    p = os.path.normpath(os.path.join(SCRIPT_DIR, fmp))
    return p if os.path.exists(p) else None

def safe_filename(title: str, artist: str) -> str:
    return re.sub(r'[^\w\-]', '_', f"{title}_{artist}")[:60]

def find_downloaded(out_base: str):
    for ext in ('.mp3', '.m4a', '.opus', '.webm', '.flac'):
        if os.path.exists(out_base + ext):
            return out_base + ext
    exts = ('.mp3', '.m4a', '.opus', '.webm', '.flac')
    matches = [m for m in glob.glob(out_base + '*') if m.lower().endswith(exts)]
    return matches[0] if matches else None

def get_duration(path: str) -> float:
    try:
        ffprobe = FFPROBE_EXE if os.path.exists(FFPROBE_EXE) else (shutil.which('ffprobe') or 'ffprobe')
        r = subprocess.run(
            [ffprobe, '-v', 'quiet', '-print_format', 'json', '-show_format', path],
            capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=15
        )
        return float(json.loads(r.stdout)['format']['duration'])
    except Exception:
        return 0.0

def check_and_clean(fp: str, idx, df) -> bool:
    dur = get_duration(fp)
    if dur > MAX_DURATION:
        print(f"  ✗ 时长 {dur/60:.1f}min > 10min，丢弃")
        try: os.remove(fp)
        except: pass
        df.at[idx, 'full_song_path'] = 'TOO_LONG'
        return False
    print(f"  时长: {dur/60:.1f}min ✓")
    return True

def _parse_float(v):
    try:
        f = float(v)
        return f if not (f != f) else None
    except (TypeError, ValueError):
        return None

def _segment_duration(row) -> float | None:
    ms = _parse_float(row.get('music_start'))
    me = _parse_float(row.get('music_end'))
    if ms is not None and me is not None and me > ms:
        return me - ms
    vs = _parse_float(row.get('video_start'))
    ve = _parse_float(row.get('video_end'))
    if vs is not None and ve is not None and ve > vs:
        return ve - vs
    v = _parse_float(row.get('video_segment_duration')) or _parse_float(row.get('video_total_duration'))
    return v if v and v > 0 else None

def set_song_span(df, idx, offset):
    dur = _segment_duration(df.loc[idx])
    df.at[idx, 'song_offset'] = offset
    df.at[idx, 'song_start'] = offset
    df.at[idx, 'music_start'] = offset
    if dur is not None:
        end = round(offset + dur, 3)
        df.at[idx, 'song_end'] = end
        df.at[idx, 'music_end'] = end

def crop_audio(audio_path: str, start, end) -> str | None:
    """裁剪 [start, end] 到临时 mp3（16k 单声道），失败返回 None"""
    s = _parse_float(start); e = _parse_float(end)
    if s is None or e is None or e - s < 3:
        return None
    tmp = tempfile.mktemp(suffix='_clip.mp3')
    ffmpeg = FFMPEG_EXE if os.path.exists(FFMPEG_EXE) else 'ffmpeg'
    r = subprocess.run(
        [ffmpeg, '-y', '-ss', str(s), '-t', str(e - s),
         '-i', audio_path, '-ac', '1', '-ar', '16000', '-q:a', '5', tmp],
        capture_output=True
    )
    if r.returncode == 0 and os.path.exists(tmp):
        return tmp
    try: os.remove(tmp)
    except: pass
    return None

def decode_audio_window(audio_path: str, offset=0.0, duration=None, sr=11025) -> str | None:
    """用 ffmpeg 解码成临时 wav，避免 librosa 直接读 mp3 时卡在后端解码器。"""
    tmp = tempfile.mktemp(suffix='_decode.wav')
    ffmpeg = FFMPEG_EXE if os.path.exists(FFMPEG_EXE) else shutil.which('ffmpeg')
    if not ffmpeg:
        return None
    cmd = [ffmpeg, '-y']
    off = _parse_float(offset) or 0.0
    dur = _parse_float(duration)
    if off > 0:
        cmd += ['-ss', str(off)]
    if dur and dur > 0:
        cmd += ['-t', str(dur)]
    cmd += ['-i', audio_path, '-ac', '1', '-ar', str(sr), '-f', 'wav', tmp]
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=30)
    except Exception:
        return None
    if r.returncode == 0 and os.path.exists(tmp) and os.path.getsize(tmp) > 1024:
        return tmp
    try: os.remove(tmp)
    except: pass
    return None

def load_wav_pcm(path: str):
    import numpy as np
    with wave.open(path, 'rb') as wf:
        channels = wf.getnchannels()
        sample_width = wf.getsampwidth()
        frames = wf.getnframes()
        sr = wf.getframerate()
        raw = wf.readframes(frames)
    if sample_width == 2:
        y = np.frombuffer(raw, dtype='<i2').astype('float32') / 32768.0
    elif sample_width == 4:
        y = np.frombuffer(raw, dtype='<i4').astype('float32') / 2147483648.0
    else:
        y = np.frombuffer(raw, dtype='uint8').astype('float32')
        y = (y - 128.0) / 128.0
    if channels > 1:
        y = y.reshape(-1, channels).mean(axis=1)
    return y, sr

def norm_key(title: str, artist: str) -> str:
    """归一化 标题+歌手 作为投票 key"""
    t = re.sub(r'[\s\(\)\[\]【】（）\-_·,，。.!！?？\'"]+', '', str(title).lower())
    a = re.sub(r'[\s\(\)\[\]【】（）\-_·,，。.!！?？\'"]+', '', str(artist).lower())
    return f"{t}::{a}"

def similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, str(a).lower(), str(b).lower()).ratio()

def _with_retry(fn, tries=3, base_delay=1.5, label=''):
    """网络请求重试（应对 10054 连接重置等瞬时故障）"""
    for k in range(tries):
        try:
            return fn()
        except Exception as e:
            if k == tries - 1:
                print(f"    {label} 重试{tries}次仍失败: {e}")
                return None
            print(f"    {label} 失败({e.__class__.__name__})，{base_delay*(k+1):.0f}s 后重试...")
            _time.sleep(base_delay * (k + 1))

# ── 1a. 单窗口 Shazam ────────────────────────────────────────────────────
async def shazam_once(audio_path: str, win_start, win_end):
    import io, contextlib
    from shazamio import Shazam
    tmp = crop_audio(audio_path, win_start, win_end)
    clip_path = tmp if tmp else audio_path
    result = None
    try:
        for _attempt in (1, 2):
            try:
                with contextlib.redirect_stderr(io.StringIO()):
                    result = await Shazam().recognize(clip_path)
                break
            except Exception:
                if _attempt == 2: raise
                await asyncio.sleep(2)
        if result and 'track' in result:
            t = result['track']
            offset = 0.0
            if result.get('matches'):
                offset = float(result['matches'][0].get('offset', 0))
            return {
                'title':  t.get('title', '').strip(),
                'artist': t.get('subtitle', '').strip(),
                'genre':  t.get('genres', {}).get('primary', '').strip(),
                'offset': offset,          # 窗口起点在原曲中的位置
            }
    except Exception as e:
        print(f"    Shazam error: {e}")
    finally:
        if tmp:
            try: os.remove(tmp)
            except: pass
    return None

def make_windows(ms, me):
    """把 [ms, me] 切成 1~3 个识别窗口"""
    ms = _parse_float(ms); me = _parse_float(me)
    if ms is None or me is None or me <= ms:
        return []
    L = me - ms
    if L <= WIN_LEN + 2:
        return [(ms, me)]
    if L <= WIN_LEN * 2.4:
        return [(ms, ms + WIN_LEN), (me - WIN_LEN, me)]
    mid = (ms + me) / 2
    return [(ms, ms + WIN_LEN),
            (mid - WIN_LEN / 2, mid + WIN_LEN / 2),
            (me - WIN_LEN, me)]

def vote_results(results, windows, ms):
    """
    results[i] 对应 windows[i]（None=失败）。
    返回 (info, confidence, note)；info=None 表示无可采信结果。
    offset 统一换算为「music_start 处对应原曲位置」的中位数。
    """
    valid = [(r, w) for r, w in zip(results, windows) if r and r.get('title')]
    n_win = len(windows)
    if not valid:
        return None, '', ''
    buckets = {}
    for r, w in valid:
        buckets.setdefault(norm_key(r['title'], r['artist']), []).append((r, w))
    best_key = max(buckets, key=lambda k: len(buckets[k]))
    grp = buckets[best_key]
    votes = len(grp)
    # offset: 每个窗口的 offset - (win_start - music_start)，取中位数
    offs = sorted(max(0.0, r['offset'] - (w[0] - ms)) for r, w in grp)
    med  = offs[len(offs) // 2] if len(offs) % 2 else (offs[len(offs)//2 - 1] + offs[len(offs)//2]) / 2
    info = dict(grp[0][0]); info['offset'] = med
    if len(buckets) == 1:
        conf = f"vote-{votes}/{n_win}" if n_win > 1 else "single"
        return info, conf, ''
    if votes >= 2:
        others = [buckets[k][0][0] for k in buckets if k != best_key]
        note = '其他候选: ' + '; '.join(f"{o['title']}—{o['artist']}" for o in others)
        return info, f"vote-{votes}/{n_win}", note
    # 全部不一致 → 冲突，交给 ACRCloud 仲裁
    cands = '; '.join(f"{r['title']}—{r['artist']}" for r, _ in valid)
    return None, 'conflict', f'Shazam冲突: {cands}'

# ── 1b. ACRCloud 兜底 ────────────────────────────────────────────────────
def _load_acr_config():
    if not os.path.exists(ACR_CONFIG):
        return None
    try:
        cfg = json.load(open(ACR_CONFIG, encoding='utf-8'))
        if cfg.get('access_key') and 'YOUR_' not in cfg['access_key']:
            return cfg
    except Exception as e:
        print(f"    acrcloud_config.json 解析失败: {e}")
    return None

def acr_identify(audio_path: str, win_start, win_end):
    """ACRCloud HTTP 识别；返回与 shazam_once 相同结构或 None"""
    cfg = _load_acr_config()
    if not cfg:
        return None
    tmp = crop_audio(audio_path, win_start, win_end)
    clip = tmp if tmp else audio_path
    try:
        data = open(clip, 'rb').read()
        ts = str(int(_time.time()))
        sign_str = '\n'.join(['POST', '/v1/identify', cfg['access_key'],
                              'audio', '1', ts])
        sign = base64.b64encode(hmac.new(cfg['access_secret'].encode(),
                                         sign_str.encode(), hashlib.sha1).digest()).decode()
        def _post():
            _prx = '' if cfg['host'].endswith('.cn') else \
                   (YT_PROXY.replace('socks5://', 'socks5h://') if YT_PROXY else '')
            return requests.post(
                f"https://{cfg['host']}/v1/identify",
                files={'sample': ('clip.mp3', data)},
                data={'access_key': cfg['access_key'], 'sample_bytes': len(data),
                      'timestamp': ts, 'signature': sign,
                      'data_type': 'audio', 'signature_version': '1'},
                proxies=({'http': _prx, 'https': _prx} if _prx else None),
                timeout=30).json()
        j = _with_retry(_post, tries=3, base_delay=2, label='ACRCloud')
        if not j:
            return None
        music = (j.get('metadata') or {}).get('music') or []
        if not music:
            return None
        m = music[0]
        clip_dur = _parse_float(win_end) - _parse_float(win_start) \
                   if _parse_float(win_end) and _parse_float(win_start) else WIN_LEN
        play_off = float(m.get('play_offset_ms', 0)) / 1000.0
        ext = m.get('external_ids') or {}
        return {
            'title':  (m.get('title') or '').strip(),
            'artist': ((m.get('artists') or [{}])[0].get('name') or '').strip(),
            'genre':  ((m.get('genres') or [{}])[0].get('name') or '').strip()
                      if m.get('genres') else '',
            'offset': max(0.0, play_off - clip_dur),   # play_offset 是样本末尾位置
        }
    except Exception as e:
        print(f"    ACRCloud error: {e}")
        return None
    finally:
        if tmp:
            try: os.remove(tmp)
            except: pass

# ── 1c. 组合识别 ─────────────────────────────────────────────────────────
async def identify(audio_clip, ms, me):
    """多窗口投票 + ACRCloud 兜底/仲裁。返回 (info, confidence, note)"""
    windows = make_windows(ms, me)
    if not windows:
        windows = [(None, None)]
    results = []
    for i, (ws, we) in enumerate(windows):
        r = await shazam_once(audio_clip, ws, we)
        results.append(r)
        if r and r.get('title'):
            print(f"    窗口{i+1} [{ws if ws is None else round(ws,1)}~"
                  f"{we if we is None else round(we,1)}s]: {r['title']} — {r['artist']}")
        else:
            print(f"    窗口{i+1}: 无结果")
        if i < len(windows) - 1:
            await asyncio.sleep(0.8)

    info, conf, note = vote_results(results, windows, _parse_float(ms) or 0.0)
    if info:
        return info, conf, note

    # Shazam 全败或冲突 → ACRCloud
    mid_w = windows[len(windows) // 2]
    acr = acr_identify(audio_clip, mid_w[0], mid_w[1])
    if acr and acr['title']:
        acr_off_at_ms = max(0.0, acr['offset'] - ((mid_w[0] or 0) - (_parse_float(ms) or 0)))
        acr['offset'] = acr_off_at_ms
        if conf == 'conflict':
            # 与 shazam 候选之一吻合 → 仲裁成功
            for r in results:
                if r and norm_key(r['title'], r['artist']) == norm_key(acr['title'], acr['artist']):
                    return acr, 'acr-confirm', note
            return acr, 'acrcloud', note + ' | ACR另给: ' + f"{acr['title']}—{acr['artist']}"
        return acr, 'acrcloud', note
    if conf == 'conflict':
        return None, 'conflict', note
    return None, '', note

# ── 2. QQ Music 搜索（相似度择优）───────────────────────────────────────
def qqmusic_search(title: str, artist: str):
    url = "https://c.y.qq.com/soso/fcgi-bin/search_for_qq_cp"
    params = {'w': f"{title} {artist}".strip(), 'format': 'json', 'n': 10, 'p': 1, 'cr': 1}
    headers = {'Referer': 'https://y.qq.com/', 'User-Agent': 'Mozilla/5.0'}
    try:
        j = _with_retry(lambda: requests.get(url, params=params, headers=headers,
                                             proxies={'http': None, 'https': None},
                                             timeout=10).json(),
                        tries=3, base_delay=1.5, label='QQ搜索')
        if not j: return None
        songs = j.get('data', {}).get('song', {}).get('list', [])
        best, best_score = None, 0.0
        for s in songs:
            interval = s.get('interval', 0) or 0
            if interval > MAX_DURATION:
                continue
            singers = '/'.join(x.get('name', '') for x in (s.get('singer') or []))
            score = similarity(title, s.get('songname', '')) * 0.7 + \
                    similarity(artist, singers) * 0.3
            if score > best_score:
                best, best_score = s, score
        if best and best_score >= 0.45:
            singers = '/'.join(x.get('name','') for x in (best.get('singer') or []))
            print(f"    QQ匹配: {best.get('songname')} — {singers} "
                  f"(相似度 {best_score:.2f}, {best.get('interval',0)}s)")
            return best.get('songmid', '')
        if songs:
            print(f"    QQ搜索有结果但相似度过低(最高 {best_score:.2f})，跳过")
    except Exception as e:
        print(f"    QQ search error: {e}")
    return None

# ── 3. yt-dlp 下载 ──────────────────────────────────────────────────────
_PP = [{'key': 'FFmpegExtractAudio', 'preferredcodec': 'mp3', 'preferredquality': '320'}]

def _ydl(source: str, out_base: str, label: str, extra_opts: dict = {}) -> bool:
    """子进程跑 yt-dlp + 墙钟硬超时（烂代理滴流数据时库内超时不会触发，只能杀进程）"""
    import tempfile
    tmp_cookie = None
    extra_opts = dict(extra_opts or {})
    if extra_opts.get('cookiefile'):
        try:
            fd, tmp_cookie = tempfile.mkstemp(
                suffix='.txt', prefix='qqmusic_cookies_', dir=OUT_DIR
            )
            os.close(fd)
            shutil.copy2(extra_opts['cookiefile'], tmp_cookie)
            extra_opts['cookiefile'] = tmp_cookie
        except Exception as e:
            print(f"    {label} cookie temp copy failed: {e}")
    cmd = [sys.executable, '-m', 'yt_dlp',
           '-f', 'bestaudio/best', '-x', '--audio-format', 'mp3',
           '--audio-quality', '320K',
           '-o', out_base + '.%(ext)s',
           '--no-playlist', '--quiet', '--no-warnings',
           '--socket-timeout', '15', '--retries', '2', '--fragment-retries', '2']
    if extra_opts.get('cookiefile'):
        cmd += ['--cookies', extra_opts['cookiefile']]
    if extra_opts.get('cookiesfrombrowser'):
        cmd += ['--cookies-from-browser', extra_opts['cookiesfrombrowser'][0]]
    if extra_opts.get('external_downloader'):
        cmd += ['--downloader', extra_opts['external_downloader']]
    if extra_opts.get('external_downloader') and os.path.basename(str(extra_opts.get('external_downloader'))).lower() in ('aria2c', 'aria2c.exe'):
        cmd += ['--downloader-args', 'aria2c:-x 8 -s 8 -k 1M --max-tries=3 --retry-wait=1 --summary-interval=0']
    for header in extra_opts.get('add_headers', []):
        cmd += ['--add-header', header]
    if 'proxy' in extra_opts:
        cmd += ['--proxy', extra_opts['proxy']]   # '' = 强制直连
    cmd.append(source)
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=DL_TIMEOUT,
                           encoding='utf-8', errors='replace')
        if r.returncode != 0 and r.stderr:
            err = r.stderr.strip().splitlines()
            if err: print(f"    {label} error: {err[-1][:150]}")
    except subprocess.TimeoutExpired:
        print(f"    {label} 超过 {DL_TIMEOUT}s 硬超时 → 杀进程换下一来源")
    except Exception as e:
        print(f"    {label} error: {e}")
    finally:
        if tmp_cookie:
            try:
                os.remove(tmp_cookie)
            except Exception:
                pass
    for junk in glob.glob(out_base + '*.part') + glob.glob(out_base + '*.ytdl'):
        try: os.remove(junk)
        except: pass
    return find_downloaded(out_base) is not None

def download_ytmusic(title, artist, out_base):
    extra = {'proxy': YT_PROXY} if YT_PROXY else {}
    return _ydl(f"https://music.youtube.com/search?q={requests.utils.quote(title+' '+artist)}",
                out_base, "YouTube Music", extra) or \
           _ydl(f"ytsearch1:{title} {artist}", out_base, "YouTube (fallback)", extra)

def download_youtube_search(title, artist, out_base):
    extra = {'proxy': YT_PROXY} if YT_PROXY else {}
    queries = [
        f"{title} {artist}",
        f"{title} {artist} audio",
        f"{title} {artist} lyrics",
    ]
    for q in queries:
        if _ydl(f"ytsearch1:{q}", out_base, "YouTube Search", extra):
            return True
    return False

def download_qqmusic(song_mid: str, out_base: str) -> bool:
    url = f"https://y.qq.com/n/ryqq/songDetail/{song_mid}"
    if os.path.exists(COOKIES_FILE):
        aria2c = _aria2c_path()
        if aria2c:
            if _ydl(url, out_base, "QQ Music (cookies file + aria2c)",
                    {'cookiefile': COOKIES_FILE, 'proxy': '',
                     'external_downloader': aria2c}):
                return True
        if _ydl(url, out_base, "QQ Music (cookies file)",
                {'cookiefile': COOKIES_FILE, 'proxy': ''}):
            return True
    for browser in ('chrome', 'edge', 'firefox'):
        try:
            if _ydl(url, out_base, f"QQ Music ({browser})", {'cookiesfrombrowser': (browser,)}):
                return True
        except Exception:
            pass
    return _ydl(url, out_base, "QQ Music (no-cookie)", {'proxy': ''})

def download_bilibili(title, artist, out_base):
    headers = [
        'Referer:https://www.bilibili.com/',
        'User-Agent:Mozilla/5.0',
    ]
    opts = {'proxy': '', 'add_headers': headers}
    if os.path.exists(BILI_COOKIES_FILE):
        if _ydl(f"bilisearch1:{title} {artist}", out_base, "Bilibili (cookies file)", {
            'proxy': '',
            'add_headers': headers,
            'cookiefile': BILI_COOKIES_FILE,
        }):
            return True
    return _ydl(f"bilisearch1:{title} {artist}", out_base, "Bilibili", opts)

# ── 4. chroma 指纹校验 + 精确 offset ─────────────────────────────────────
def chroma_xcorr(C_song, C_clip):
    """
    C_song: 12×N, C_clip: 12×M（每帧 L2 归一化后）。
    返回 (best_lag_frames, score 0~1)。纯 numpy，滑动余弦相似均值。
    """
    import numpy as np
    N, M = C_song.shape[1], C_clip.shape[1]
    if N < M or M < 4:
        return 0, 0.0
    # FFT 互相关: 对 12 个 bin 分别相关后求和
    L = N - M + 1
    total = np.zeros(L)
    for b in range(12):
        c = np.correlate(C_song[b], C_clip[b], mode='valid')
        total += c
    total /= M  # 每帧相关系数均值 ∈[-1,1]，无关≈0
    lag = int(total.argmax())
    return lag, float(total[lag])

def envelope_xcorr(y_song, y_clip, sr, frame_sec=0.08):
    import numpy as np
    frame = max(1, int(sr * frame_sec))
    def env(y):
        n = len(y) // frame
        if n <= 1:
            return np.array([], dtype='float32')
        y = y[:n * frame]
        e = np.sqrt((y.reshape(n, frame) ** 2).mean(axis=1))
        e = np.diff(e, prepend=e[:1])
        e = np.maximum(e, 0)
        e = e - e.mean()
        std = e.std()
        return e / (std if std > 1e-6 else 1.0)
    Es = env(y_song)
    Ec = env(y_clip)
    N, M = len(Es), len(Ec)
    if N < M or M < 4:
        return 0.0, 0.0
    corr = np.correlate(Es, Ec, mode='valid') / max(M, 1)
    lag = int(corr.argmax())
    return lag * frame / sr, float(corr[lag])

def fast_chroma_features(y, sr, n_fft=4096, hop=1024):
    import numpy as np
    from scipy.signal import stft
    if len(y) < n_fft:
        return np.empty((12, 0), dtype='float32')
    _, _, Z = stft(
        y,
        fs=sr,
        window='hann',
        nperseg=n_fft,
        noverlap=n_fft - hop,
        nfft=n_fft,
        boundary=None,
        padded=False,
    )
    mag = np.abs(Z).astype('float32')
    freqs = np.fft.rfftfreq(n_fft, 1.0 / sr)
    valid = (freqs >= 65.0) & (freqs <= 2100.0)
    freqs = freqs[valid]
    mag = mag[valid]
    if mag.size == 0:
        return np.empty((12, 0), dtype='float32')
    midi = np.rint(69 + 12 * np.log2(freqs / 440.0)).astype(int)
    chroma_idx = np.mod(midi, 12)
    C = np.zeros((12, mag.shape[1]), dtype='float32')
    for k in range(12):
        mask = chroma_idx == k
        if np.any(mask):
            C[k] = mag[mask].sum(axis=0)
    return _norm_chroma(C)

def _norm_chroma(C):
    """逐帧去均值 + L2 归一化 → 帧间点积为相关系数，无关内容基线≈0"""
    import numpy as np
    C = C - C.mean(axis=0, keepdims=True)
    n = np.linalg.norm(C, axis=0, keepdims=True)
    n[n == 0] = 1.0
    return C / n

def librosa_dtw_align(y_song, y_clip, sr):
    """Slower fallback used when fast chroma xcorr is uncertain."""
    import numpy as np
    import librosa
    hop = 512
    max_clip = int(sr * 35)
    if len(y_clip) > max_clip:
        y_clip = y_clip[:max_clip]
    if len(y_clip) < sr * 3 or len(y_song) < len(y_clip):
        return None, None
    y_clip = librosa.util.normalize(np.nan_to_num(y_clip).astype('float32'))
    y_song = librosa.util.normalize(np.nan_to_num(y_song).astype('float32'))

    Cc = librosa.feature.chroma_stft(y=y_clip, sr=sr, hop_length=hop)
    Cs = librosa.feature.chroma_stft(y=y_song, sr=sr, hop_length=hop)
    Cc = np.nan_to_num(Cc) + 1e-8
    Cs = np.nan_to_num(Cs) + 1e-8
    D, wp = librosa.sequence.dtw(X=Cc, Y=Cs, metric='cosine', subseq=True)
    if wp is None or len(wp) == 0:
        return None, None
    start_frame = int(wp[np.argmin(wp[:, 0])][1])
    end_frame = int(wp[np.argmax(wp[:, 0])][1])
    if end_frame < start_frame:
        start_frame, end_frame = end_frame, start_frame
    clip_sec = len(y_clip) / float(sr)
    aligned_sec = (end_frame - start_frame + 1) * hop / float(sr)
    if aligned_sec < clip_sec * 0.35:
        return None, None
    path_cost = float(np.mean([D[i, j] for i, j in wp if i < D.shape[0] and j < D.shape[1]]))
    score = max(0.0, min(1.0, 1.0 - path_cost))
    return start_frame * hop / float(sr), score

def verify_and_align(song_path, clip_audio, ms, me):
    """
    返回 (refined_offset, score) 或 (None, None)。
    使用当前视频原声音频与完整歌曲做对齐；ms/me 只作为一个可能的搜索提示。
    """
    tmp = None
    decoded_clip = None
    decoded_songs = []
    src = clip_audio
    try:
        sr = 11025
        clip_duration = None
        try:
            import soundfile as sf
            info = sf.info(clip_audio)
            if info.samplerate:
                clip_duration = float(info.frames) / float(info.samplerate)
        except Exception:
            pass
        ms_f = _parse_float(ms)
        me_f = _parse_float(me)
        if (
            clip_duration is not None and ms_f is not None and me_f is not None
            and 0 <= ms_f < me_f <= clip_duration + 0.25
        ):
            tmp = crop_audio(clip_audio, ms_f, me_f)
            src = tmp if tmp else clip_audio
        decoded_clip = decode_audio_window(src, offset=0, duration=45, sr=sr)
        if not decoded_clip:
            return None, None
        src = decoded_clip
        y_clip, sr_clip = load_wav_pcm(src)
        sr = sr_clip

        def _score_window(offset, duration):
            decoded_song = decode_audio_window(song_path, offset=offset, duration=duration, sr=sr)
            if not decoded_song:
                return None
            decoded_songs.append(decoded_song)
            y_song, _ = load_wav_pcm(decoded_song)
            if len(y_clip) < sr * 3 or len(y_song) < len(y_clip):
                return None
            try:
                Cc = fast_chroma_features(y_clip, sr)
                Cs = fast_chroma_features(y_song, sr)
                lag, score = chroma_xcorr(Cs, Cc)
                return {
                    'offset': float(offset) + lag * 1024 / float(sr),
                    'score': float(score),
                    'y_song': y_song,
                    'base_offset': float(offset),
                }
            except Exception as e:
                print(f"    chroma 快速对齐失败: {e}")
                return None

        guess_start = _parse_float(ms)
        guess_end = _parse_float(me)
        song_duration = get_duration(song_path) or 0.0
        full_limit = min(song_duration if song_duration > 0 else 600.0, 600.0)
        windows = []
        if guess_start is not None and guess_end is not None and guess_end > guess_start:
            pad = 25.0
            start = max(0.0, guess_start - pad)
            end = guess_end + pad
            if song_duration > 0:
                end = min(song_duration, end)
            if end > start + 5:
                windows.append((start, end - start))
        windows.append((0.0, full_limit))

        best = None
        for offset, duration in windows:
            cand = _score_window(offset, duration)
            if cand and (best is None or cand['score'] > best['score']):
                best = cand
        if not best:
            return None, None

        if best['score'] < MATCH_GOOD and os.environ.get('MGSV_ALIGN_DTW') == '1':
            try:
                clip_sec = len(y_clip) / float(sr)
                local_start = max(0.0, best['offset'] - 35.0)
                local_duration = min((song_duration - local_start) if song_duration > 0 else clip_sec + 70.0,
                                     clip_sec + 70.0)
                local_song = decode_audio_window(song_path, offset=local_start, duration=local_duration, sr=sr)
                if local_song:
                    decoded_songs.append(local_song)
                    y_local, _ = load_wav_pcm(local_song)
                    dtw_lag, dtw_score = librosa_dtw_align(y_local, y_clip, sr)
                    if dtw_lag is not None and dtw_score is not None:
                        return round(local_start + dtw_lag, 2), round(max(float(dtw_score), best['score']), 3)
            except Exception as e:
                print(f"    DTW 复核失败，使用快速对齐结果: {e}")
            try:
                lag_sec, env_score = envelope_xcorr(best['y_song'], y_clip, sr)
                if env_score > best['score']:
                    return round(best['base_offset'] + lag_sec, 2), round(float(env_score), 3)
            except Exception:
                pass
        return round(best['offset'], 2), round(best['score'], 3)
    except Exception as e:
        print(f"    指纹校验失败: {e}")
        return None, None
    finally:
        for p in ([tmp, decoded_clip] + decoded_songs):
            if p:
                try: os.remove(p)
                except: pass

def apply_alignment(idx, df, song_path, clip_audio, ms, me):
    off, score = verify_and_align(song_path, clip_audio, ms, me)
    if off is None:
        return
    df.at[idx, 'match_score'] = score
    if score >= MATCH_GOOD:
        old = _parse_float(df.at[idx, 'song_offset']) or 0
        set_song_span(df, idx, off)
        print(f"  ✓ 指纹匹配 {score:.2f}  offset: {old:.1f}s → {off:.1f}s")
    elif score >= MATCH_MIN:
        set_song_span(df, idx, off)
        print(f"  ⚠ 指纹匹配一般 ({score:.2f})，offset={off:.1f}s，建议人工核对")
    else:
        note = str(df.at[idx, 'recog_note'] or '')
        df.at[idx, 'recog_note'] = (note + ' | ' if note else '') + f'指纹匹配过低({score:.2f})，可能下错歌'
        print(f"  ✗ 指纹匹配过低 ({score:.2f})！可能下错歌，offset 未更新")

# ── 保存 ─────────────────────────────────────────────────────────────────
def save_excel(df):
    """原子保存：写 .tmp.xlsx → 备份旧版 → 替换"""
    import shutil
    tmp = os.path.splitext(EXCEL)[0] + '.tmp.xlsx'
    df.to_excel(tmp, index=False)
    try:
        if os.path.exists(EXCEL):
            shutil.copy2(EXCEL, os.path.splitext(EXCEL)[0] + '.backup.xlsx')
    except Exception as e:
        print(f"备份失败(不影响保存): {e}")
    os.replace(tmp, EXCEL)

# ── 主流程 ──────────────────────────────────────────────────────────────
async def process_row(idx: int, row, df):
    print(f"\n[{idx}] {row.get('creator_name', '')} — {str(row.get('video_title', ''))[:40]}")

    ms = row.get('music_start')
    me = row.get('music_end')

    existing = str(row.get('full_song_path', '') or '').strip()
    already_ok = (existing not in ('', 'nan', 'DOWNLOAD_FAILED', 'UNIDENTIFIED',
                                   'TOO_LONG', 'REJECTED', 'NO_CLIP')
                  and os.path.exists(existing))

    audio_clip = resolve_path(str(row.get('full_music_path', '') or ''))
    if not audio_clip:
        if not already_ok:
            df.at[idx, 'full_song_path'] = 'NO_CLIP'
        return

    # ── 已有原曲：只做指纹校验 + 精算 offset（不联网）──────────────────
    if already_ok:
        print("  ✓ 已有原曲 → 指纹校验/精算 offset ...")
        apply_alignment(idx, df, existing, audio_clip, ms, me)
        return

    # ── 新行：识曲（投票+兜底）→ 搜索下载 → 校验 ─────────────────────
    prev_title = str(row.get('song_title', '') or '').strip()
    if existing == 'DOWNLOAD_FAILED' and prev_title and prev_title != 'nan':
        info = {'title': prev_title,
                'artist': str(row.get('song_artist', '') or '').strip(),
                'genre': '',
                'offset': _parse_float(row.get('song_offset')) or 0.0}
        conf = str(row.get('recog_confidence', '') or '')
        note = ''
        print(f"  ↻ 沿用已识别: {info['title']} — {info['artist']}，直接重试下载")
    else:
        info, conf, note = await identify(audio_clip, ms, me)
    if note:
        df.at[idx, 'recog_note'] = note
    if not info or not info['title']:
        print(f"  ✗ 识别失败 → UNIDENTIFIED  {('['+note+']') if note else ''}")
        df.at[idx, 'full_song_path'] = 'UNIDENTIFIED'
        df.at[idx, 'recog_confidence'] = conf or ''
        return

    print(f"  ✓ [{conf}] {info['title']} — {info['artist']}  offset≈{info['offset']:.1f}s")
    df.at[idx, 'song_title']  = info['title']
    df.at[idx, 'song_artist'] = info['artist']
    set_song_span(df, idx, round(info['offset'], 2))
    df.at[idx, 'recog_confidence'] = conf
    if info.get('genre') and str(row.get('genre', '')).strip() in ('', 'nan'):
        df.at[idx, 'genre'] = info['genre']

    out_base = os.path.join(OUT_DIR, safe_filename(info['title'], info['artist']))

    def _try_qq():
        song_mid = qqmusic_search(info['title'], info['artist'])
        if song_mid:
            print(f"  QQ Music ({song_mid})...")
            download_qqmusic(song_mid, out_base)
    def _try_yt():
        print("  YouTube Music...")
        download_ytmusic(info['title'], info['artist'], out_base)
    def _try_bili():
        print("  Bilibili...")
        download_bilibili(info['title'], info['artist'], out_base)
    _steps = {'qq': _try_qq, 'yt': _try_yt, 'bili': _try_bili}
    order = [s for s in SOURCES if s in _steps]
    if glob.glob(out_base + '*.wrong*') and 'yt' in order:
        order.remove('yt'); order.insert(0, 'yt')   # 下错过 → 优先换源
    chain = [_steps[s] for s in order]

    def _shelve(bad):
        import hashlib as _hl
        try:
            sig = (os.path.getsize(bad),
                   _hl.md5(open(bad, 'rb').read(262144)).hexdigest())
            for w in glob.glob(out_base + '*.wrong*'):
                if w != bad and os.path.getsize(w) == sig[0] and \
                   _hl.md5(open(w, 'rb').read(262144)).hexdigest() == sig[1]:
                    os.remove(bad)   # 内容重复，不囤积
                    return
        except Exception:
            pass
        dst, k = bad + '.wrong', 1
        while os.path.exists(dst):
            dst = bad + f'.wrong{k}'; k += 1
        try: os.replace(bad, dst)
        except Exception as e: print(f"    改名失败: {e}")

    n_wrong = len(glob.glob(out_base + '*.wrong*'))
    if n_wrong >= 4:
        print(f"  ⛔ 已试 {n_wrong} 个版本均未通过指纹 → 熔断，转人工处理")
        df.at[idx, 'full_song_path'] = 'DOWNLOAD_FAILED'
        note = str(df.at[idx, 'recog_note'] or '')
        tag = f'已自动试{n_wrong}版本均不匹配，疑似识别错误，建议手机QQ音乐识曲'
        if tag not in note:
            df.at[idx, 'recog_note'] = (note + ' | ' if note and note != 'nan' else '') + tag
        return
    fp = find_downloaded(out_base)
    if fp:
        print(f"  ✓ 本地已有: {os.path.basename(fp)}")
    tested_wrong = set()
    while True:
        if not fp:
            for w in sorted(glob.glob(out_base + '*.wrong*')):
                orig = re.sub(r'\.wrong\d*$', '', w)
                if orig in tested_wrong or os.path.exists(orig):
                    continue
                tested_wrong.add(orig)
                try: os.replace(w, orig)
                except Exception: continue
                fp = orig
                print(f"  ↩ 复验此前疑似下错的文件: {os.path.basename(orig)}")
                break
        if not fp:
            if not chain:
                print("  ✗ 全部来源失败或均未通过指纹校验")
                df.at[idx, 'full_song_path'] = 'DOWNLOAD_FAILED'
                return
            chain.pop(0)()
            fp = find_downloaded(out_base)
            if not fp:
                continue
            print(f"  ✓ 下载完成: {os.path.basename(fp)}")
        if not check_and_clean(fp, idx, df):
            fp = None
            continue
        off, score = verify_and_align(fp, audio_clip, ms, me)
        if score is not None and score < MATCH_MIN:
            print(f"  ✗ 指纹 {score:.2f} 过低 → 判定下错歌，自动换下一来源")
            _shelve(fp)
            fp = None
            continue
        df.at[idx, 'full_song_path'] = fp
        if score is None:
            print("  ✓ 已保存（librosa 缺失，未做指纹校验）")
        else:
            df.at[idx, 'match_score'] = score
            set_song_span(df, idx, off)
            tag = '✓ 指纹匹配' if score >= MATCH_GOOD else '⚠ 指纹匹配一般，建议人工核对'
            print(f"  {tag} {score:.2f}  offset={off:.1f}s")
        return

async def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument('--redownload-low', action='store_true',
                    help='清除指纹匹配过低的行并重新识曲+换源下载（旧文件改名 .wrong 保留）')
    args = ap.parse_args()
    if not _load_acr_config():
        print("ℹ ACRCloud 未配置（可选）：把密钥填入 acrcloud_config.json 可启用兜底识别")
    df = pd.read_excel(EXCEL, keep_default_na=False)
    if 'isrc' in df.columns:
        df = df.drop(columns=['isrc'])
    for col in ['song_title','song_artist','genre','song_verified',
                'full_song_path','recog_confidence','recog_note']:
        if col not in df.columns: df[col] = ''
        df[col] = df[col].astype(object)
    if 'song_offset' not in df.columns: df['song_offset'] = 0.0
    if 'song_start' not in df.columns: df['song_start'] = None
    if 'song_end' not in df.columns: df['song_end'] = None
    if 'match_score' not in df.columns: df['match_score'] = None
    df['song_start'] = pd.to_numeric(df['song_start'], errors='coerce')
    df['song_end'] = pd.to_numeric(df['song_end'], errors='coerce')
    df['match_score'] = pd.to_numeric(df['match_score'], errors='coerce')

    if args.redownload_low:
        for idx in df.index:
            fp = str(df.at[idx, 'full_song_path'] or '')
            sc = df.at[idx, 'match_score']
            if os.path.exists(fp) and sc == sc and sc < MATCH_MIN:
                try: os.replace(fp, fp + '.wrong')
                except Exception as e: print(f"[{idx}] 改名失败: {e}")
                df.at[idx, 'full_song_path'] = ''
                df.at[idx, 'match_score'] = None
                print(f"[{idx}] 指纹 {sc:.2f} 过低 → 已清除，将重新识曲+换源下载")

    for idx, row in df.iterrows():
        await process_row(idx, row, df)
        save_excel(df)
        await asyncio.sleep(1.5)

    total = len(df)
    ok  = df['full_song_path'].apply(lambda x: bool(x) and x not in
           ('','nan','UNIDENTIFIED','DOWNLOAD_FAILED','NO_CLIP','TOO_LONG','REJECTED')).sum()
    fail = df['full_song_path'].isin(['UNIDENTIFIED','DOWNLOAD_FAILED','TOO_LONG']).sum()
    real = df['full_song_path'].apply(lambda x: isinstance(x, str) and os.path.exists(x))
    scn  = pd.to_numeric(df['match_score'], errors='coerce')
    low  = int(((scn < MATCH_MIN) & real).sum())
    print(f"\n═══ 完成 ═══  成功: {ok}/{total}  需人工: {fail}  指纹存疑: {low}")

if __name__ == '__main__':
    asyncio.run(main())
