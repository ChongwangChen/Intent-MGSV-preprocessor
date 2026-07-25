#!/usr/bin/env python3
"""MGSV 人工标注工具 — 零复制/零转码版
（丰富标签 + 识曲核对 + 手动处理 + 分镜段双方案打分 + 悬浮小窗）

分段打分逻辑:
    - 是否卡点(sync_level) 由人工判断（旧数据 0/1/2 自动映射: 0→否, 1/2→是）
    - 卡点=是: 方案A = 最接近4等分的 top-3 分镜点(4段)；方案B = 最接近6等分的
      top-5 分镜点(6段)。分镜点不足时自适应；两方案相同只显示A。
    - 卡点=否: 整段一个分数(方案A单段)，方案B隐藏。
    - 分数写入 seg_scores_3 / seg_scores_5，'/' 分隔，'-' 表示该段未打分。

保存机制: 先写 .tmp 再原子替换，替换前把上一版拷成 *.backup.xlsx，
避免中断导致 Excel 截断损坏。
"""

import os, re, shutil, urllib.parse, http.server, threading, mimetypes, subprocess, sys, json, glob, time
from datetime import datetime
import gradio as gr
import pandas as pd

SCRIPT_DIR   = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.environ.get("MGSV_ROOT", SCRIPT_DIR)
EXCEL        = os.environ.get("MGSV_EXCEL", os.path.join(PROJECT_ROOT, "outputs", "MGSV_Master_Dataset.xlsx"))
SCAN_ROOT    = os.environ.get("MGSV_SCAN_ROOT", os.path.join(PROJECT_ROOT, "DouK-Source", "Volume", "Download"))
COOKIES_FILE = os.path.join(SCRIPT_DIR, "qqmusic_cookies.txt")
OUT_DIR      = os.path.join(PROJECT_ROOT, "outputs", "full_songs")
BACKUP_DIR   = os.path.join(PROJECT_ROOT, "outputs", "annotation_backups")
STATE_FILE   = os.path.join(PROJECT_ROOT, "outputs", "annotation_state.json")
ARIA2C_EXE   = os.environ.get("ARIA2C_EXE", r"E:\tools\aria2-1.37.0-win-64bit-build1\aria2c.exe")
MEDIA_PORT   = 7861

os.makedirs(OUT_DIR, exist_ok=True)
os.makedirs(BACKUP_DIR, exist_ok=True)
_LAST_HISTORY_BACKUP = 0.0
_SAVE_LOCK = threading.RLock()
_DOWNLOAD_LOCK = threading.Lock()
_LONG_TASK_LOCK = _DOWNLOAD_LOCK

def _aria2c_path():
    if os.path.exists(ARIA2C_EXE):
        return ARIA2C_EXE
    return shutil.which('aria2c')

# ── 标签分组 ───────────────────────────────────────────────────────────────
EMO_POS = ['热血','激昂','欢乐','兴奋','轻快','愉悦','甜蜜','浪漫','幸福',
           '治愈','温暖','希望','感动','放松','平静']
EMO_NEU = ['神秘','空灵','梦幻','深沉','庄重','克制','高级','孤独','怀旧']
EMO_NEG = ['伤感','悲伤','压抑','紧张','悬疑','恐怖','愤怒','焦虑','绝望']
STY_PER = ['青春','成长','校园','恋爱','回忆','励志']
STY_VIS = ['高级感','电影感','科技感','未来感','赛博朋克','质感','极简']
STY_ATM = ['梦幻','文艺','松弛','治愈系','温馨','清新','夏日感','冬日感','慵懒']
STY_CUL = ['国风','中国风','古风','日系','韩系','欧美感','二次元']
STY_EDI = ['卡点','转场','混剪','高燃','节奏感强','踩鼓点','剧情感','大片感']
STY_CON = ['旅行','冒险','探索','都市','街头','潮流','时尚','电竞']
SCE_DAI = ['散步','跑步','运动','健身','开车','骑行','通勤','学习','工作','阅读','写作','睡前','日常Vlog']
SCE_SOC = ['表白','恋爱','情侣','约会','婚礼','毕业','聚会','生日','纪念日']
SCE_CRE = ['旅行Vlog','探店','美食','宠物','风景','城市记录','露营','航拍',
           '街拍','开箱','测评','剧情短片','搞笑视频','宣传片',
           '舞蹈','手势舞','古风舞蹈','变装','走秀',
           '游戏剪辑','动漫剪辑','影视剪辑','MV混剪','音乐现场']
SCE_SPE = ['夜晚','清晨','黄昏','雨天','海边','公路','森林','雪景','夏天','冬天','舞台','节日']

VOCAL_OPTS = ['Unmarked','None','Partial','Full']
SCORE_OPTS = ['1','2','3','4','5']   # 分段配乐契合度
SEG_A_SLOTS = 4                      # 方案A: 最多3点→4段（整段模式=1段）
SEG_B_SLOTS = 6                      # 方案B: 最多5点→6段
_BAD_PATHS = {'','nan','UNIDENTIFIED','DOWNLOAD_FAILED','NO_CLIP','TOO_LONG','REJECTED'}

# ── 媒体服务器 ─────────────────────────────────────────────────────────────
SEG_A_SLOTS = 12
SEG_B_SLOTS = 12
_BAD_PATHS = {'','nan','UNIDENTIFIED','DOWNLOAD_FAILED','NO_CLIP','TOO_LONG','REJECTED'}

_ALLOWED = [os.path.abspath(SCRIPT_DIR), os.path.abspath(SCAN_ROOT)]

def _safe(p):
    a = os.path.abspath(p)
    return any(a.startswith(r) for r in _ALLOWED)

class _MediaHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        p = urllib.parse.unquote(q.get('p',[None])[0] or '')
        if not p or not os.path.isfile(p) or not _safe(p):
            self.send_response(404); self.end_headers(); return
        mime  = mimetypes.guess_type(p)[0] or 'application/octet-stream'
        fsize = os.path.getsize(p)
        rng   = self.headers.get('Range','')
        try:
            if rng:
                s,e = rng.strip().split('=')[1].split('-')
                start = int(s) if s else 0
                end   = min(int(e) if e else fsize-1, fsize-1)
                self.send_response(206)
                self.send_header('Content-Type', mime)
                self.send_header('Content-Range', f'bytes {start}-{end}/{fsize}')
                self.send_header('Content-Length', str(end-start+1))
                self.send_header('Access-Control-Allow-Origin','*')
                self.end_headers()
                with open(p,'rb') as f:
                    f.seek(start); self.wfile.write(f.read(end-start+1))
            else:
                self.send_response(200)
                self.send_header('Content-Type', mime)
                self.send_header('Content-Length', str(fsize))
                self.send_header('Accept-Ranges','bytes')
                self.send_header('Access-Control-Allow-Origin','*')
                self.end_headers()
                with open(p,'rb') as f:
                    while True:
                        c = f.read(65536)
                        if not c: break
                        self.wfile.write(c)
        except (BrokenPipeError, ConnectionResetError): pass
    def log_message(self,*a): pass

threading.Thread(
    target=http.server.HTTPServer(('127.0.0.1',MEDIA_PORT),_MediaHandler).serve_forever,
    daemon=True).start()
print(f"Media server: http://127.0.0.1:{MEDIA_PORT}")

# ── HTML 组件 ──────────────────────────────────────────────────────────────
def murl(path):
    if path and not os.path.isabs(str(path)):
        path = os.path.normpath(os.path.join(PROJECT_ROOT, str(path)))
    if not path or not os.path.isfile(path): return None
    version = int(os.path.getmtime(path))
    return f"http://127.0.0.1:{MEDIA_PORT}/?p={urllib.parse.quote(path)}&v={version}"

def audio_html(path):
    u = murl(path)
    if not u: return '<p style="color:#aaa">⚠ 音频未找到</p>'
    aid = 'clip-' + str(abs(hash(u)))
    return f'<audio id="{aid}" controls preload="metadata" src="{u}" style="width:100%;margin-top:6px"></audio>'

# 悬浮小窗：视频滚出视野后自动固定在右上角跟随（回滚复原）
# 轮询检测 + !important 压过行内样式；guard 保证只安装一次
_FLOAT_JS = (
    "if(!window._mgsvF){window._mgsvF=1;"
    "var s=document.createElement('style');"
    "s.textContent='video.mgsv-mini{position:fixed!important;top:10px!important;"
    "right:10px!important;left:auto!important;width:340px!important;"
    "height:auto!important;max-height:200px!important;z-index:9999!important;"
    "box-shadow:0 6px 20px rgba(0,0,0,.55);border-radius:8px}';"
    "document.head.appendChild(s);"
    "setInterval(function(){"
    "var h=document.getElementById('mgsv-vidholder');if(!h)return;"
    "var v=h.querySelector('video');if(!v)return;"
    "var r=h.getBoundingClientRect();"
    "if(r.bottom<60){v.classList.add('mgsv-mini');}"
    "else{v.classList.remove('mgsv-mini');}"
    "},300);}"
)
_FLOAT_IMG = ('<img src="data:image/gif;base64,R0lGODlhAQABAAAAACwAAAAAAQABAAA="'
              f' style="display:none" onload="{_FLOAT_JS}">')

def video_html(path):
    u = murl(path)
    if not u: return '<p style="color:#aaa">⚠ 视频未找到</p>'
    return (f'<div id="mgsv-vidholder" style="min-height:280px">'
            f'<video controls style="width:100%;max-height:280px;background:#000">'
            f'<source src="{u}"></video></div>{_FLOAT_IMG}')

def full_song_html(path, offset=0.0):
    if not path or str(path).strip() in _BAD_PATHS:
        return '<p style="color:#aaa;font-size:13px">⚠ 完整原曲未就绪</p>'
    u = murl(path)
    if not u: return '<p style="color:#aaa;font-size:13px">⚠ 原曲文件缺失</p>'
    try: t = float(offset)
    except: t = 0.0
    aid = 'song-' + str(abs(hash(u)))
    return (f'<p style="font-size:11px;color:#888;margin:2px 0">完整原曲（从 {t:.0f}s 起播，与左侧片段对比）</p>'
            f'<audio id="{aid}" controls preload="metadata" src="{u}" style="width:100%;margin-top:4px"'
            f' onloadedmetadata="this.currentTime={t:.1f}"></audio>')

def verify_status_md(row) -> str:
    fp  = str(row.get('full_song_path','') or '').strip()
    t   = str(row.get('song_title','') or '').strip()
    ar  = str(row.get('song_artist','') or '').strip()
    ver = str(row.get('song_verified','') or '').strip()
    if fp == 'UNIDENTIFIED' or not t:
        return "🔍 **识曲**: ⚠ 未识别 → 在下方手动下载或删除"
    if fp in _BAD_PATHS:
        return f"🔍 **识曲**: {t} — {ar}  |  ⚠ {fp} → 在下方手动处理"
    icon = "✅ 已确认" if ver == '是' else ("❌ 已标记有误" if ver == '否—有误' else "⏳ **待核对** ← 对比音频后确认")
    return f"🔍 **识曲**: **{t}** — {ar}  |  {icon}"

# ── 分镜段工具 ─────────────────────────────────────────────────────────────
def _num(v):
    """float 或 None（NaN/非法值→None）"""
    try:
        f = float(v)
        return f if f == f else None
    except (TypeError, ValueError):
        return None

def norm_sync(v):
    """0/1/2 旧数据映射: 0→否, ≥1→是；'是'/'否' 原样"""
    s = str(v or '').strip()
    if s in ('是','否'): return s
    n = _num(v)
    if n is not None: return '是' if n >= 1 else '否'
    return '否'

def norm_sync(v):
    s = str(v or '').strip()
    if s in ('Yes', 'No'):
        return s
    if s in ('是', 'ÊÇ', '鏄?') or s.lower() in ('yes', 'true'):
        return 'Yes'
    if s in ('否', '·ñ', '鍚?') or s.lower() in ('no', 'false'):
        return 'No'
    n = _num(v)
    if n is not None:
        return 'Yes' if n >= 1 else 'No'
    return 'No'

def is_sync_yes(v):
    return norm_sync(v) == 'Yes'

def parse_points(v):
    """'3.20/7.85' → [3.2, 7.85]（非数值项忽略，如 'NONE'/'SAME'）"""
    out = []
    for x in str(v or '').split('/'):
        x = x.strip()
        if not x: continue
        try: out.append(float(x))
        except ValueError: pass
    return sorted(out)

def _span(row):
    """Scoring and shot-review spans use the video timeline, not music_start."""
    end = _num(row.get('video_total_duration'))
    if end is None or end <= 0:
        end = _num(row.get('music_end'))
    return 0.0, end

def _bounds(pts, ms, end):
    return list(zip([ms] + pts, pts + [end]))   # 末段 end 可为 None

def get_schemes(row, sync):
    """返回 (boundsA, capA, boundsB)。
    boundsA=[] 表示需先跑检测（capA 为提示）；boundsB=None 表示无方案B"""
    ms, end = _span(row)
    if norm_sync(sync) != '是':
        return [(ms, end)], '整段打分（非卡点视频）', None
    raw3 = str(row.get('shot_points_3','') or '').strip()
    if raw3 == '' or raw3.lower() == 'nan':
        return [], '⚠ 卡点视频需先运行 `python shot_detect.py` 检测分镜点', None
    if raw3 == 'NONE':
        return [(ms, end)], '未检测到分镜点 → 整段打分', None
    pts3 = [p for p in parse_points(raw3) if p > ms and (end is None or p < end)]
    if not pts3:
        return [(ms, end)], '分镜点越界 → 整段打分', None
    boundsA = _bounds(pts3, ms, end)
    capA = f'方案A · top-{len(pts3)}（4等分取点，{len(boundsA)}段）'
    raw5 = str(row.get('shot_points_5','') or '').strip()
    boundsB = None
    if raw5 not in ('', 'SAME', 'NONE') and raw5.lower() != 'nan':
        pts5 = [p for p in parse_points(raw5) if p > ms and (end is None or p < end)]
        if pts5 and pts5 != pts3:
            boundsB = _bounds(pts5, ms, end)
    return boundsA, capA, boundsB

def _jump_btn(tag, i, a, b):
    """跳到段起点播放；到段尾自动暂停（末段 end 未知则播到结尾）"""
    if b is None:
        js  = _FLOAT_JS + (f"var v=document.querySelector('video');if(v){{"
               f"v.currentTime={a:.2f};v.play();v.ontimeupdate=null;}}")
        txt = f'▶ {tag}{i}：{a:.1f}s – 结尾'
    else:
        js  = _FLOAT_JS + (f"var v=document.querySelector('video');if(v){{"
               f"v.currentTime={a:.2f};v.play();"
               f"v.ontimeupdate=function(){{if(v.currentTime>={b:.2f}-0.05)"
               f"{{v.pause();v.ontimeupdate=null;}}}};}}")
        txt = f'▶ {tag}{i}：{a:.1f} – {b:.1f}s'
    return (f'<button onclick="{js}" '
            f'style="margin:2px;padding:4px 12px;border-radius:6px;'
            f'border:1px solid #999;cursor:pointer;font-size:13px">{txt}</button>')

_PIP_JS = ("var v=document.querySelector('video');if(v){"
           "if(document.pictureInPictureElement){document.exitPictureInPicture();}"
           "else if(v.requestPictureInPicture){v.requestPictureInPicture();}}")

def seg_html(boundsA, capA, boundsB):
    if not boundsA:
        return f'<p style="color:#c60;font-size:13px">{capA}</p>'
    h = ('<p style="font-size:11px;color:#888;margin:2px 0">点击跳段播放（到段尾自动暂停），'
         '在下方为每段打配乐契合度：1=完全不搭 … 5=非常契合　'
         f'<button onclick="{_PIP_JS}" '
         'style="padding:2px 10px;border-radius:6px;border:1px solid #999;'
         'cursor:pointer;font-size:12px">📌 视频小窗（画中画开/关）</button></p>')
    h += f'<p style="font-size:13px;margin:4px 0 0"><b>{capA}</b></p><div>'
    h += ''.join(_jump_btn('A段', i+1, a, b) for i,(a,b) in enumerate(boundsA)) + '</div>'
    if boundsB:
        h += (f'<p style="font-size:13px;margin:8px 0 0"><b>方案B · top-{len(boundsB)-1}'
              f'（6等分取点，{len(boundsB)}段）</b></p><div>')
        h += ''.join(_jump_btn('B段', i+1, a, b) for i,(a,b) in enumerate(boundsB)) + '</div>'
    return h

def parse_seg_scores(v):
    """'4/-/5' → ['4', None, '5']"""
    out = []
    for x in str(v or '').split('/'):
        x = x.strip()
        out.append(x if x in SCORE_OPTS else None)
    return out

def _radio_updates(bounds, scores, tag, slots):
    ups = []
    n = len(bounds) if bounds else 0
    for k in range(slots):
        if k < n:
            a, b = bounds[k]
            rng  = f"{a:.1f}s–结尾" if b is None else f"{a:.1f}–{b:.1f}s"
            val  = scores[k] if k < len(scores) else None
            ups.append(gr.update(visible=True, value=val,
                                 label=f"{tag}{k+1}（{rng}）"))
        else:
            ups.append(gr.update(visible=False, value=None, label=f"{tag}{k+1}"))
    return ups

def seg_outputs(row, sync):
    """(seg_area_html,) + 4个A radio + 6个B radio = 11 个值"""
    boundsA, capA, boundsB = get_schemes(row, sync)
    sA = parse_seg_scores(row.get('seg_scores_3'))
    sB = parse_seg_scores(row.get('seg_scores_5'))
    return ((seg_html(boundsA, capA, boundsB),)
            + tuple(_radio_updates(boundsA, sA, 'A段', SEG_A_SLOTS))
            + tuple(_radio_updates(boundsB or [], sB, 'B段', SEG_B_SLOTS)))

_SEG_HIDDEN = tuple(gr.update(visible=False, value=None)
                    for _ in range(SEG_A_SLOTS + SEG_B_SLOTS))
_SEG_NOOP   = tuple(gr.update() for _ in range(1 + SEG_A_SLOTS + SEG_B_SLOTS))

def get_schemes(row, sync):
    ms, end = _span(row)
    if not is_sync_yes(sync):
        return [(ms, end)], 'Whole-video score (not sync)', None
    raw3 = str(row.get('shot_points_3','') or '').strip()
    if raw3 == '' or raw3.lower() == 'nan':
        return [], 'Run shot detection first', None
    if raw3 == 'NONE':
        return [(ms, end)], 'No shot points detected; whole-video score', None
    pts3 = [p for p in parse_points(raw3) if p > ms and (end is None or p < end)]
    if not pts3:
        return [(ms, end)], 'Shot points out of range; whole-video score', None
    boundsA = _bounds(pts3, ms, end)
    capA = f'Scheme A: {len(boundsA)} segments'
    raw5 = str(row.get('shot_points_5','') or '').strip()
    boundsB = None
    if raw5 not in ('', 'SAME', 'NONE') and raw5.lower() != 'nan':
        pts5 = [p for p in parse_points(raw5) if p > ms and (end is None or p < end)]
        if pts5 and pts5 != pts3:
            boundsB = _bounds(pts5, ms, end)
    return boundsA, capA, boundsB

# ── QQ 音乐链接解析 ───────────────────────────────────────────────────────
def _qq_song_id_to_mid(song_id: str, headers=None) -> str:
    import json as _json, subprocess as _subprocess
    import requests as _req
    headers = headers or {'Referer': 'https://y.qq.com/', 'User-Agent': 'Mozilla/5.0'}
    api = "https://c.y.qq.com/v8/fcg-bin/fcg_play_single_song.fcg"
    params = {'songid': song_id, 'platform': 'yqq', 'format': 'json'}
    try:
        data = _req.get(api, params=params, headers=headers, timeout=10).json()
        mid = data['data'][0].get('mid', '')
        if mid:
            return mid
    except Exception as e:
        print(f"  song_id API via requests failed: {e}")

    # Some Windows/Python TLS stacks get closed by QQ Music, while curl works.
    # Keep this fallback narrow: it only calls the public song-id lookup API.
    try:
        query = urllib.parse.urlencode(params)
        r = _subprocess.run(
            ['curl.exe', '-L', '-s', '-A', headers['User-Agent'],
             '-e', headers['Referer'], f'{api}?{query}'],
            capture_output=True, text=True, encoding='utf-8', errors='replace',
            timeout=15
        )
        if r.returncode == 0 and r.stdout.strip():
            data = _json.loads(r.stdout)
            mid = data['data'][0].get('mid', '')
            if mid:
                return mid
        elif r.stderr:
            print(f"  song_id API via curl failed: {r.stderr.strip()[:160]}")
    except Exception as e:
        print(f"  song_id API via curl failed: {e}")
    return ''

def _qq_song_info(song_id: str = '', song_mid: str = '', headers=None):
    import json as _json, subprocess as _subprocess
    import requests as _req
    headers = headers or {'Referer': 'https://y.qq.com/', 'User-Agent': 'Mozilla/5.0'}
    api = "https://c.y.qq.com/v8/fcg-bin/fcg_play_single_song.fcg"
    params = {'platform': 'yqq', 'format': 'json'}
    if song_id:
        params['songid'] = song_id
    if song_mid:
        params['songmid'] = song_mid

    def _parse(data):
        try:
            item = (data.get('data') or [{}])[0]
            title = (item.get('name') or item.get('songname') or item.get('title') or '').strip()
            singers = item.get('singer') or item.get('singers') or []
            artist = '/'.join((s.get('name') or '').strip() for s in singers if isinstance(s, dict) and s.get('name'))
            mid = (item.get('mid') or item.get('songmid') or song_mid or '').strip()
            if title:
                return {'song_title': title, 'song_artist': artist, 'song_mid': mid}
        except Exception:
            pass
        return {}

    try:
        info = _parse(_req.get(api, params=params, headers=headers, timeout=10).json())
        if info:
            return info
    except Exception as e:
        print(f"  QQ song info via requests failed: {e}")

    try:
        query = urllib.parse.urlencode(params)
        r = _subprocess.run(
            ['curl.exe', '-L', '-s', '-A', headers['User-Agent'],
             '-e', headers['Referer'], f'{api}?{query}'],
            capture_output=True, text=True, encoding='utf-8', errors='replace',
            timeout=15
        )
        if r.returncode == 0 and r.stdout.strip():
            return _parse(_json.loads(r.stdout))
        if r.stderr:
            print(f"  QQ song info via curl failed: {r.stderr.strip()[:160]}")
    except Exception as e:
        print(f"  QQ song info via curl failed: {e}")
    return {}

def _resolve_qqmusic_url(raw: str) -> str:
    """
    把各种 QQ 音乐链接/ID 转成 yt-dlp 可用的 URL：
      - 纯 song_mid（字母数字，不带 http）→ 直接拼
      - ryqq_v2/songDetail/{数字 song_id}  → 查 API 换 song_mid
      - ryqq/songDetail/{song_mid}          → 标准格式，原样用
      - 其他 http URL                       → 原样传给 yt-dlp
    """
    import subprocess as _subprocess
    import requests as _req
    raw = raw.strip().split('#')[0]   # 去掉 #wechat_redirect 等 fragment
    headers = {'Referer': 'https://y.qq.com/', 'User-Agent': 'Mozilla/5.0'}

    if not raw.startswith('http'):
        m = re.search(r'[A-Za-z0-9]{14,16}', raw)
        return f"https://y.qq.com/n/ryqq/songDetail/{m.group()}" if m else raw

    # QQ Music share links (for example c6.y.qq.com/base/fcgi-bin/u?...)
    # redirect to ryqq_v2/songDetail/<numeric song_id>. yt-dlp does not
    # support that v2 URL directly, so expand the share link before parsing.
    if re.search(r'https?://c\d*\.y\.qq\.com/base/fcgi-bin/u\?', raw):
        try:
            resp = _req.get(raw, headers=headers, allow_redirects=True, timeout=10)
            final_url = resp.url.split('#')[0]
            if final_url and final_url != raw:
                print(f"  QQ share link -> {final_url}")
                raw = final_url
        except Exception as e:
            print(f"  QQ share link resolve failed: {e}")
            try:
                cmd = ['curl.exe', '-L', '-s', '-o', 'NUL', '-w', '%{url_effective}',
                       '-A', headers['User-Agent'], '-e', headers['Referer']]
                if os.path.exists(COOKIES_FILE):
                    cmd += ['-b', COOKIES_FILE]
                cmd.append(raw)
                r = _subprocess.run(
                    cmd, capture_output=True, text=True, encoding='utf-8',
                    errors='replace', timeout=15
                )
                final_url = (r.stdout or '').strip().split('#')[0]
                if r.returncode == 0 and final_url and final_url != raw:
                    print(f"  QQ share link via curl -> {final_url}")
                    raw = final_url
                elif r.stderr:
                    print(f"  QQ share link via curl failed: {r.stderr.strip()[:160]}")
            except Exception as e2:
                print(f"  QQ share link via curl failed: {e2}")

    m_id = re.search(r'/songDetail/(\d{6,12})', raw)
    if m_id:
        song_id = m_id.group(1)
        mid = _qq_song_id_to_mid(song_id, headers)
        if mid:
            print(f"  song_id {song_id} -> song_mid {mid}")
            return f"https://y.qq.com/n/ryqq/songDetail/{mid}"
        print(f"  song_id resolve failed: {song_id}")

    m_mid = re.search(r'/songDetail/([A-Za-z0-9]{14,16})', raw)
    if m_mid:
        return f"https://y.qq.com/n/ryqq/songDetail/{m_mid.group(1)}"

    return raw

def _qq_song_info_from_input(raw: str):
    raw = (raw or '').strip()
    if not raw:
        return {}
    resolved = _resolve_qqmusic_url(raw)
    resolved_mid = ''
    m_mid = re.search(r'/songDetail/([A-Za-z0-9]{14,16})', resolved)
    if m_mid:
        resolved_mid = m_mid.group(1)
        info = _qq_song_info(song_mid=m_mid.group(1))
        if info:
            if not info.get('song_mid'):
                info['song_mid'] = m_mid.group(1)
            return info
    m_id = re.search(r'/songDetail/(\d{6,12})', raw) or re.search(r'/songDetail/(\d{6,12})', resolved)
    if m_id:
        info = _qq_song_info(song_id=m_id.group(1))
        if info:
            return info
    if not raw.startswith('http'):
        m = re.search(r'[A-Za-z0-9]{14,16}', raw)
        if m:
            info = _qq_song_info(song_mid=m.group())
            if info:
                info.setdefault('song_mid', m.group())
                return info
    try:
        import yt_dlp, tempfile as _tempfile
        tmp_cookie = None
        opts = {'quiet': True, 'no_warnings': True, 'noplaylist': True, 'socket_timeout': 15}
        if os.path.exists(COOKIES_FILE):
            fd, tmp_cookie = _tempfile.mkstemp(
                suffix='.txt', prefix='qqmusic_cookies_', dir=OUT_DIR
            )
            os.close(fd)
            shutil.copy2(COOKIES_FILE, tmp_cookie)
            opts['cookiefile'] = tmp_cookie
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(resolved, download=False)
            title = str(info.get('title') or '').strip()
            artists = info.get('artists') or []
            artist = '/'.join(str(x).strip() for x in artists if str(x).strip())
            if not artist:
                artist = str(info.get('artist') or info.get('uploader') or '').strip()
            if title:
                return {'song_title': title, 'song_artist': artist, 'song_mid': resolved_mid}
        finally:
            if tmp_cookie:
                try:
                    os.remove(tmp_cookie)
                except Exception:
                    pass
    except Exception as e:
        print(f"  QQ song info via yt-dlp failed: {str(e).strip().splitlines()[-1][:160]}")
    return {}

# ── QQ 音乐手动下载 ────────────────────────────────────────────────────────
def qdl(url_or_mid: str, out_base: str):
    import yt_dlp, glob as _glob, tempfile as _tempfile
    url = _resolve_qqmusic_url(url_or_mid)
    print(f"  qdl -> {url}")
    base_opts = {
        'format': 'bestaudio/best',
        'postprocessors': [{'key':'FFmpegExtractAudio','preferredcodec':'mp3','preferredquality':'320'}],
        'outtmpl': out_base + '.%(ext)s',
        'noplaylist': True, 'quiet': True, 'no_warnings': True,
        'retries': 5, 'fragment_retries': 5, 'extractor_retries': 5, 'socket_timeout': 25,
        'continuedl': True,
    }

    def _found():
        for ext in ('.mp3','.m4a','.opus','.webm','.flac'):
            if os.path.exists(out_base + ext):
                return out_base + ext
        hits = [p for p in _glob.glob(out_base + '*')
                if not p.lower().endswith(('.part', '.ytdl'))]
        return hits[0] if hits else None

    def _cookie_file_has_login(path: str) -> bool:
        login_names = {
            'uin', 'skey', 'p_uin', 'p_skey', 'pt4_token',
            'qm_keyst', 'qqmusic_key', 'wxuin', 'wxrefresh_token'
        }
        try:
            with open(path, encoding='utf-8', errors='replace') as f:
                for line in f:
                    if not line.strip() or line.startswith('#'):
                        continue
                    parts = line.rstrip('\n').split('\t')
                    if len(parts) >= 7 and parts[5] in login_names:
                        return True
        except Exception:
            pass
        return False

    def _cookie_file_login_status(path: str):
        critical = {'uin', 'qm_keyst', 'qqmusic_key', 'psrf_qqaccess_token'}
        seen = {}
        now = time.time()
        try:
            with open(path, encoding='utf-8', errors='replace') as f:
                for line in f:
                    if not line.strip() or line.startswith('#'):
                        continue
                    parts = line.rstrip('\n').split('\t')
                    if len(parts) < 7 or parts[5] not in critical:
                        continue
                    try:
                        exp = int(parts[4])
                    except Exception:
                        exp = 0
                    seen[parts[5]] = exp
        except Exception as e:
            return False, f"无法读取 cookie 文件: {e}"
        valid = [k for k, exp in seen.items() if exp == 0 or exp > now]
        if 'qqmusic_key' in valid or 'qm_keyst' in valid:
            return True, ""
        if seen:
            latest = max(seen.values())
            expired_at = datetime.fromtimestamp(latest).strftime('%Y-%m-%d %H:%M:%S') if latest else 'session'
            return False, f"qqmusic_cookies.txt 登录 cookie 已过期，最近过期时间: {expired_at}"
        return False, "qqmusic_cookies.txt 没有 QQ Music 登录 cookie"

    auth_attempts = []
    if os.path.exists(COOKIES_FILE):
        cookie_ok, cookie_note = _cookie_file_login_status(COOKIES_FILE)
        if not cookie_ok:
            print(f"  qdl note: {cookie_note}")
        else:
            aria2c = _aria2c_path()
            if aria2c:
                aria2_opts = {
                    'cookiefile': COOKIES_FILE,
                    'external_downloader': aria2c,
                    'external_downloader_args': {
                        'aria2c': ['-x', '8', '-s', '8', '-k', '1M',
                                   '--max-tries=5', '--retry-wait=1',
                                   '--summary-interval=0']
                    }
                }
                for n in range(1, 4):
                    auth_attempts.append((f"cookies file + aria2c downloader ({n}/3)", dict(aria2_opts)))
            auth_attempts.append(("cookies file", {'cookiefile': COOKIES_FILE}))
            auth_attempts.append(("cookies file + curl downloader", {
                'cookiefile': COOKIES_FILE,
                'external_downloader': 'curl',
                'external_downloader_args': {
                    'curl': ['--retry', '4', '--retry-all-errors', '--retry-delay', '1',
                             '--connect-timeout', '10']
                }
            }))
    for browser in ('chrome', 'edge', 'firefox'):
        auth_attempts.append((browser, {'cookiesfrombrowser': (browser,)}))
    auth_attempts.append(("no cookie", {}))

    def _download_once(target_url: str, auth_opts: dict):
        tmp_cookie = None
        opts = dict(base_opts)
        if auth_opts.get('cookiefile'):
            try:
                fd, tmp_cookie = _tempfile.mkstemp(
                    suffix='.txt', prefix='qqmusic_cookies_', dir=OUT_DIR
                )
                os.close(fd)
                shutil.copy2(auth_opts['cookiefile'], tmp_cookie)
                auth_opts = dict(auth_opts)
                auth_opts['cookiefile'] = tmp_cookie
            except Exception as e:
                print(f"  qdl warning: cookie temp copy failed: {e}")
        opts.update(auth_opts)
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                ydl.download([target_url])
        finally:
            if tmp_cookie:
                try:
                    os.remove(tmp_cookie)
                except Exception:
                    pass

    def _try_download(target_url: str):
        last_error = None
        saw_locked_browser_cookie = False
        for label, auth_opts in auth_attempts:
            try:
                print(f"  qdl auth: {label}")
                _download_once(target_url, auth_opts)
                fp = _found()
                if fp:
                    return fp, None
            except Exception as e:
                last_error = e
                msg = str(e).strip().splitlines()
                tail = msg[-1] if msg else str(e)
                if 'Could not copy Chrome cookie database' in str(e):
                    saw_locked_browser_cookie = True
                print(f"  qdl {label} error: {tail[:180]}")
                if 'aria2c downloader' in label and (
                    'ConnectionResetError' in str(e)
                    or 'Unable to download JSON metadata' in str(e)
                    or 'TransportError' in str(e)
                ):
                    time.sleep(1.5)
        if saw_locked_browser_cookie:
            print("  qdl note: close Chrome/Edge completely, then retry so yt-dlp can read browser cookies")
        return None, last_error

    fp, err = _try_download(url)
    if fp:
        return fp

    msg = str(err or '')
    retry_url = ''
    m_id = re.search(r'/songDetail/(\d{6,12})', msg)
    if m_id:
        retry_url = _resolve_qqmusic_url(f"https://y.qq.com/n/ryqq_v2/songDetail/{m_id.group(1)}")
    if retry_url and retry_url != url:
        print(f"  qdl retry -> {retry_url}")
        fp, err = _try_download(retry_url)
        if fp:
            return fp
    if err:
        print(f"qdl error: {err}")
    return _found()

# ── 数据 ───────────────────────────────────────────────────────────────────
def load_df():
    df = pd.read_excel(EXCEL, keep_default_na=False)
    for col in ['vocal_presence','emotion','style','usage_scene',
                'sync_level','genre','song_title','song_artist',
                'song_verified','full_song_path','qq_song_mid',
                'shot_points','shot_points_3','shot_points_5',
                'seg_scores_3','seg_scores_5']:
        if col not in df.columns: df[col] = ''
        df[col] = df[col].astype(object)
    if 'song_offset' not in df.columns: df['song_offset'] = 0.0
    if 'song_start' not in df.columns: df['song_start'] = None
    if 'song_end' not in df.columns: df['song_end'] = None
    if 'match_score' not in df.columns: df['match_score'] = None
    df['song_start'] = pd.to_numeric(df['song_start'], errors='coerce')
    df['song_end'] = pd.to_numeric(df['song_end'], errors='coerce')
    df['match_score'] = pd.to_numeric(df['match_score'], errors='coerce')
    for old in ('rhythm_points','seg_scores'):
        if old in df.columns: df = df.drop(columns=[old])
    if 'isrc' in df.columns:
        df = df.drop(columns=['isrc'])
    return df

def save_df(df):
    global _LAST_HISTORY_BACKUP
    """原子保存：写 .tmp → 备份旧版 → 替换，避免中断截断 Excel"""
    with _SAVE_LOCK:
        if os.path.exists(EXCEL):
            try:
                latest = pd.read_excel(EXCEL, keep_default_na=False)
                preserve_cols = [
                    'vocal_presence', 'genre', 'emotion', 'style', 'usage_scene',
                    'full_song_path', 'song_verified', 'shot_points',
                    'shot_points_3', 'shot_points_5', 'seg_scores_3', 'seg_scores_5',
                ]
                for col in preserve_cols:
                    if col not in df.columns or col not in latest.columns:
                        continue
                    for idx in df.index.intersection(latest.index):
                        outgoing = df.at[idx, col]
                        existing = latest.at[idx, col]
                        if _safe_blank_for_col(col, outgoing) and not _safe_blank_for_col(col, existing):
                            df.at[idx, col] = existing
                if 'vocal_presence' in df.columns:
                    vocal_recovery = None
                    for idx in df.index:
                        if _norm_vocal_value(df.at[idx, 'vocal_presence']):
                            continue
                        if vocal_recovery is None:
                            vocal_recovery = _build_vocal_recovery_map()
                        recovered = vocal_recovery.get(int(idx), '')
                        if recovered:
                            df.at[idx, 'vocal_presence'] = recovered
                            print(f"  vocal recovered before save: row {int(idx)} -> {recovered}")
            except Exception as e:
                print(f"并发保存保护读取失败(继续保存): {e}")

        tmp = os.path.splitext(EXCEL)[0] + '.tmp.xlsx'
        df.to_excel(tmp, index=False)
        try:
            if os.path.exists(EXCEL):
                shutil.copy2(EXCEL, os.path.splitext(EXCEL)[0] + '.backup.xlsx')
                now = datetime.now().timestamp()
                if now - _LAST_HISTORY_BACKUP >= 300:
                    stamp = datetime.now().strftime('%Y%m%d-%H%M%S')
                    hist = os.path.join(BACKUP_DIR, f"MGSV_Master_Dataset.{stamp}.xlsx")
                    shutil.copy2(EXCEL, hist)
                    _LAST_HISTORY_BACKUP = now
        except Exception as e:
            print(f"备份失败(不影响保存): {e}")
        os.replace(tmp, EXCEL)

def resolve_music_path(fmp):
    if not fmp or str(fmp) in ('nan','None'): return None
    fmp = fmp.replace('\\', os.sep)
    if os.path.isabs(fmp): return fmp if os.path.exists(fmp) else None
    p = os.path.normpath(os.path.join(SCRIPT_DIR, fmp))
    return p if os.path.exists(p) else None

_BAD_NAME_VALUES = {'', 'nan', 'none', 'null', 'nat'}

def _clean_filename_part(v, limit=40):
    try:
        if pd.isna(v):
            return ''
    except Exception:
        pass
    s = str(v or '').strip()
    if s.lower() in _BAD_NAME_VALUES:
        return ''
    s = re.sub(r'[^\w\-]+', '_', s).strip('_')
    return s[:limit]

def _unique_out_base(base):
    exts = ('.mp3', '.m4a', '.wav', '.flac', '.opus', '.webm')
    for k in range(1, 1000):
        cand = base if k == 1 else f"{base}_{k}"
        if not any(os.path.exists(cand + ext) for ext in exts):
            return cand
    return f"{base}_{int(pd.Timestamp.now().timestamp())}"

def _manual_download_base(row, idx, url):
    title = _clean_filename_part(row.get('song_title'), 40)
    artist = _clean_filename_part(row.get('song_artist'), 24)
    parts = [p for p in (title, artist) if p]
    if not parts:
        m = (re.search(r'/songDetail/([A-Za-z0-9]+)', url or '') or
             re.search(r'[?&]__=([A-Za-z0-9_-]+)', url or ''))
        parts = [(m.group(1)[:32] if m else 'manual')]
    stem = re.sub(r'_+', '_', '_'.join(parts)).strip('_')[:90]
    return _unique_out_base(os.path.join(OUT_DIR, stem))

def _audio_metadata(path):
    try:
        from mutagen.easyid3 import EasyID3
        tags = EasyID3(path)
        return {
            'title': str((tags.get('title') or [''])[0]).strip(),
            'artist': str((tags.get('artist') or [''])[0]).strip(),
            'genre': str((tags.get('genre') or [''])[0]).strip(),
        }
    except Exception:
        return {'title': '', 'artist': '', 'genre': ''}

def _audio_metadata_title_artist(path):
    meta = _audio_metadata(path)
    return meta['title'], meta['artist']

def _canonical_song_base(row):
    title = _clean_filename_part(row.get('song_title'), 48)
    artist = _clean_filename_part(row.get('song_artist'), 28)
    parts = [p for p in (title, artist) if p]
    if not parts:
        return ''
    return os.path.join(OUT_DIR, re.sub(r'_+', '_', '_'.join(parts)).strip('_')[:100])

def _existing_exact_song_file(row):
    base = _canonical_song_base(row)
    if not base:
        return None
    for ext in ('.mp3', '.m4a', '.wav', '.flac', '.opus', '.webm'):
        fp = base + ext
        if os.path.exists(fp):
            return fp
    return None

def _existing_song_by_qq_mid(row, df=None, cur_idx=None):
    mid = str(row.get('qq_song_mid') or '').strip()
    if not mid or df is None or 'qq_song_mid' not in df.columns or 'full_song_path' not in df.columns:
        return None
    for i, r in df.iterrows():
        if cur_idx is not None and int(i) == int(cur_idx):
            continue
        if str(r.get('qq_song_mid') or '').strip() != mid:
            continue
        fp = str(r.get('full_song_path') or '').strip()
        if fp and fp.lower() not in _BAD_PATHS and os.path.exists(fp):
            return fp
    return None

def _song_key(title, artist):
    title = str(title or '').strip()
    artist = str(artist or '').strip()
    if title.lower() in _BAD_NAME_VALUES:
        title = ''
    if artist.lower() in _BAD_NAME_VALUES:
        artist = ''
    if not title:
        return ''
    text = f"{title} {artist}".lower()
    text = re.sub(r'\bcover\b|伴奏|翻唱|完整版|原版|歌词|lyrics|audio', ' ', text)
    text = re.sub(r'[^\w\u4e00-\u9fff]+', ' ', text)
    return re.sub(r'\s+', ' ', text).strip()

def _existing_full_song(row, df=None, cur_idx=None):
    by_mid = _existing_song_by_qq_mid(row, df, cur_idx)
    if by_mid:
        return by_mid
    exact = _existing_exact_song_file(row)
    if exact:
        return exact
    wanted = _song_key(row.get('song_title'), row.get('song_artist'))
    if not wanted:
        return None
    if df is not None and 'full_song_path' in df.columns:
        for i, r in df.iterrows():
            if cur_idx is not None and int(i) == int(cur_idx):
                continue
            if _song_key(r.get('song_title'), r.get('song_artist')) != wanted:
                continue
            fp = str(r.get('full_song_path') or '').strip()
            if fp and fp.lower() not in _BAD_PATHS and os.path.exists(fp):
                return fp
    title_only = _song_key(row.get('song_title'), '')
    artist_given = bool(_clean_filename_part(row.get('song_artist')))
    title_only_matches = []
    for fp in glob.glob(os.path.join(OUT_DIR, '*')):
        if not os.path.isfile(fp) or os.path.splitext(fp)[1].lower() not in ('.mp3', '.m4a', '.wav', '.flac', '.opus', '.webm'):
            continue
        meta = _audio_metadata(fp)
        meta_key = _song_key(meta.get('title'), meta.get('artist'))
        if meta_key == wanted:
            return fp
        if not artist_given and title_only and _song_key(meta.get('title'), '') == title_only:
            title_only_matches.append(fp)
    if not artist_given and len(title_only_matches) == 1:
        return title_only_matches[0]
    return None

def _detect_genre_from_audio(path):
    if os.environ.get('MGSV_DETECT_GENRE_ON_DOWNLOAD') != '1':
        return ''
    try:
        from auto import run_genre_detection
        genre = run_genre_detection(path, confidence_threshold=0.45)
        return genre.strip() if genre else ''
    except Exception as e:
        print(f"  genre detection failed: {e}")
        return ''

def _rename_download_from_metadata(path, idx):
    title, artist = _audio_metadata_title_artist(path)
    title_part = _clean_filename_part(title, 48)
    artist_part = _clean_filename_part(artist, 28)
    if not title_part and not artist_part:
        return path, title, artist
    ext = os.path.splitext(path)[1] or '.mp3'
    stem = re.sub(r'_+', '_', f"{title_part}_{artist_part}").strip('_')[:100]
    canonical_path = os.path.join(OUT_DIR, stem) + ext
    if os.path.exists(canonical_path) and os.path.abspath(canonical_path) != os.path.abspath(path):
        try:
            os.remove(path)
        except Exception as e:
            print(f"  duplicate cleanup failed: {e}")
        return canonical_path, title, artist
    new_path = canonical_path
    if os.path.abspath(new_path) != os.path.abspath(path):
        try:
            os.replace(path, new_path)
            path = new_path
        except Exception as e:
            print(f"  metadata rename failed: {e}")
    return path, title, artist

def _download_by_title_fallback(row, out_base):
    title = str(row.get('song_title') or '').strip()
    artist = str(row.get('song_artist') or '').strip()
    if title.lower() in _BAD_NAME_VALUES:
        title = ''
    if artist.lower() in _BAD_NAME_VALUES:
        artist = ''
    if not title:
        return None, '没有可用于搜索的歌名'
    try:
        import music_fetch
        old_timeout = getattr(music_fetch, 'DL_TIMEOUT', 20)
        print(f"  fallback search -> {title} {artist}".strip())
        has_cjk = bool(re.search(r'[\u4e00-\u9fff]', f'{title}{artist}'))
        if has_cjk:
            music_fetch.DL_TIMEOUT = 25
            sources = [
                ('Bilibili', music_fetch.download_bilibili),
                ('YouTube Search', music_fetch.download_youtube_search),
                ('YouTube Music', music_fetch.download_ytmusic),
            ]
            print("  fallback mode: 中文歌仅快速尝试 Bilibili；失败后请粘贴更明确的链接")
        else:
            music_fetch.DL_TIMEOUT = 30
            sources = [
                ('YouTube Music', music_fetch.download_ytmusic),
                ('Bilibili', music_fetch.download_bilibili),
            ]
        for label, fn in sources:
            print(f"  fallback trying {label} ...")
            if fn(title, artist, out_base):
                return music_fetch.find_downloaded(out_base), f'{label} 搜索'
            print(f"  fallback {label} 未成功，换下一来源")
    except Exception as e:
        return None, f'搜索兜底失败：{e}'
    finally:
        try:
            music_fetch.DL_TIMEOUT = old_timeout
        except Exception:
            pass
    return None, '搜索兜底未找到可下载音频'

def _parse_manual_song_text(text):
    s = (text or '').strip()
    if not s:
        return {}
    if re.match(r'^[a-zA-Z][a-zA-Z0-9+.-]*://', s) or re.search(r'\by\.qq\.com\b', s):
        return {}
    out = {}
    patterns = {
        'song_title': r'(?:歌曲名|歌名|title|song)\s*[:：]\s*([^,，\n]+)',
        'song_artist': r'(?:歌手名|歌手|artist|singer)\s*[:：]\s*([^,，\n]+)',
        'album': r'(?:专辑名|专辑|album)\s*[:：]\s*([^,，\n]+)',
    }
    for key, pat in patterns.items():
        m = re.search(pat, s, flags=re.I)
        if m:
            out[key] = m.group(1).strip()
    if not out and len(s) < 120:
        parts = [p.strip() for p in re.split(r'[-–—|/，,]', s) if p.strip()]
        if parts:
            out['song_title'] = parts[0]
        if len(parts) > 1:
            out['song_artist'] = parts[1]
    return out

def build_path_index(scan_root):
    idx = {}
    if not os.path.exists(scan_root): return idx
    for root, _, files in os.walk(scan_root):
        vf = [f for f in files if f.lower().endswith(('.mp4','.avi','.mov'))]
        af = [f for f in files if f.lower().endswith(('.m4a','.mp3','.wav'))]
        for v in vf:
            idx[v] = (os.path.join(root,v), os.path.join(root,af[0]) if af else None)
    return idx

print("Scanning videos...")
PATH_INDEX = build_path_index(SCAN_ROOT)
print(f"  Found {len(PATH_INDEX)} videos")

def _blank(v):
    try:
        if pd.isna(v):
            return True
    except Exception:
        pass
    text = str(v or '').strip()
    return text == '' or text.lower() in ('nan', 'none', 'null')

def _song_verified_ok(value):
    text = str(value or '').strip()
    return text in ('\u662f', '\u062a\u0627', 'Yes', 'yes', 'true', '1')

def _needs_song_confirmation(row):
    fp = str(row.get('full_song_path', '') or '').strip()
    if not fp or fp in _BAD_PATHS or fp.lower() in ('nan', 'none', 'null'):
        return False
    return not _song_verified_ok(row.get('song_verified', ''))

def _safe_blank_for_col(col, value):
    text = str(value or '').strip()
    low = text.lower()
    if col == 'vocal_presence':
        return text not in ('None', 'Partial', 'Full')
    return text == '' or low in ('nan', 'none', 'null', 'unmarked')

def _norm_vocal_value(value):
    text = str(value or '').strip()
    return text if text in ('None', 'Partial', 'Full') else ''

def _recent_backup_files(limit=8):
    files = []
    try:
        files.extend(glob.glob(os.path.join(BACKUP_DIR, 'MGSV_Master_Dataset.*.xlsx')))
    except Exception:
        pass
    try:
        simple_backup = os.path.splitext(EXCEL)[0] + '.backup.xlsx'
        if os.path.exists(simple_backup):
            files.append(simple_backup)
    except Exception:
        pass
    files = [f for f in files if os.path.exists(f)]
    files.sort(key=lambda p: os.path.getmtime(p), reverse=True)
    return files[:limit]

def _recover_vocal_from_backups(idx):
    recovery = _build_vocal_recovery_map()
    return recovery.get(int(idx), '')

def _build_vocal_recovery_map():
    recovery = {}
    for fp in reversed(_recent_backup_files()):
        try:
            bdf = pd.read_excel(fp, keep_default_na=False)
            if 'vocal_presence' not in bdf.columns:
                continue
            for idx in bdf.index:
                vocal = _norm_vocal_value(bdf.at[idx, 'vocal_presence'])
                if vocal:
                    recovery[int(idx)] = vocal
        except Exception:
            continue
    return recovery

def _row_required_missing(row):
    missing = []
    vocal = str(row.get('vocal_presence', '') or '').strip()
    if vocal not in ('None', 'Partial', 'Full'):
        missing.append('vocal_presence')
    if _blank(row.get('genre', '')):
        missing.append('Genre')
    if _blank(row.get('emotion', '')):
        missing.append('Emotion')
    if _blank(row.get('style', '')):
        missing.append('Style')
    if _blank(row.get('usage_scene', '')):
        missing.append('Usage Scene')
    if _needs_song_confirmation(row):
        missing.append('song confirmation')

    sync_norm = norm_sync(row.get('sync_level'))
    boundsA, _, _ = get_schemes(row, sync_norm)
    scores = parse_seg_scores(row.get('seg_scores_3'))
    if is_sync_yes(sync_norm) and not boundsA:
        missing.append('shot detection')
    need_scores = min(len(boundsA), SEG_A_SLOTS) if boundsA else 0
    if need_scores and any(k >= len(scores) or scores[k] not in SCORE_OPTS for k in range(need_scores)):
        missing.append('segment scores')
    return missing

def get_unannotated_indices(df):
    return [idx for idx, row in df.iterrows() if _row_required_missing(row)]

def _queue_after(df, cur_idx, include_current=False):
    q = get_unannotated_indices(df)
    if not include_current:
        q = [i for i in q if i != cur_idx]
    later = [i for i in q if int(i) > int(cur_idx)]
    target = later[0] if later else (q[0] if q else -1)
    return q, target

def _read_last_idx():
    try:
        with open(STATE_FILE, 'r', encoding='utf-8') as f:
            return int(json.load(f).get('last_idx', -1))
    except Exception:
        return -1

def _write_last_idx(idx):
    try:
        with open(STATE_FILE, 'w', encoding='utf-8') as f:
            json.dump({'last_idx': int(idx)}, f)
    except Exception as e:
        print(f"annotation state save failed: {e}")

def _resume_target(queue):
    if not queue:
        return -1
    last_idx = _read_last_idx()
    later = [i for i in queue if int(i) >= last_idx]
    return later[0] if later else queue[0]

def parse_labels(v):
    if not v or isinstance(v, float): return []
    return [x.strip() for x in str(v).split('/') if x.strip()]

def format_labels(lst): return '/'.join(lst) if lst else ''
def pick(s, g): return [x for x in g if x in s]

def _get_offset(row):
    try: return float(row.get('song_offset', 0) or 0)
    except: return 0.0

def _segment_duration(row):
    for a, b in (('music_start', 'music_end'), ('video_start', 'video_end')):
        av, bv = _num(row.get(a)), _num(row.get(b))
        if av is not None and bv is not None and bv > av:
            return bv - av
    v = _num(row.get('video_segment_duration')) or _num(row.get('video_total_duration'))
    return v if v and v > 0 else None

def _set_song_span(df, idx, offset):
    dur = _segment_duration(df.loc[idx])
    df.at[idx, 'song_offset'] = offset
    df.at[idx, 'song_start'] = offset
    df.at[idx, 'music_start'] = offset
    if dur is not None:
        end = round(offset + dur, 3)
        df.at[idx, 'song_end'] = end
        df.at[idx, 'music_end'] = end

def _verify_and_align_timeout(song_fp, audio_p, ms, me, timeout=75):
    code = (
        "import json,sys;"
        "import music_fetch;"
        "off,score=music_fetch.verify_and_align(sys.argv[1],sys.argv[2],sys.argv[3],sys.argv[4]);"
        "print(json.dumps({'off':off,'score':score}, ensure_ascii=False))"
    )
    try:
        env = os.environ.copy()
        env['PYTHONIOENCODING'] = 'utf-8'
        env['PYTHONUTF8'] = '1'
        r = subprocess.run(
            [sys.executable, '-c', code, song_fp, audio_p, str(ms), str(me)],
            cwd=SCRIPT_DIR,
            capture_output=True,
            text=True,
            encoding='utf-8',
            errors='replace',
            env=env,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return None, None, f"自动对齐超过 {timeout}s，保留原 offset"
    except Exception as e:
        return None, None, f"自动对齐启动失败：{e}"
    for line in reversed((r.stdout or '').splitlines()):
        line = line.strip()
        if line.startswith('{') and line.endswith('}'):
            try:
                data = json.loads(line)
                return data.get('off'), data.get('score'), ''
            except Exception:
                break
    tail = ((r.stderr or r.stdout or '').strip().splitlines() or [''])[-1]
    return None, None, f"自动对齐失败：{tail[:120]}"

def load_record(df, idx):
    row    = df.loc[idx]
    v_file = str(row.get('video_id','') or '')
    vpaths = PATH_INDEX.get(v_file,(None,None))
    video_p = vpaths[0] if vpaths[0] and os.path.exists(vpaths[0]) else None
    audio_p = resolve_music_path(str(row.get('full_music_path','') or ''))
    if audio_p is None:
        audio_p = vpaths[1] if vpaths[1] and os.path.exists(vpaths[1]) else None
    info = (f"**创作者**: {row.get('creator_name','-')}  "
            f"**标题**: {str(row.get('video_title','-') or '-')[:50]}  "
            f"**BPM**: {row.get('bpm','-')}  **节奏**: {row.get('rhythm_category','-')}\n\n"
            f"**标签**: {str(row.get('hashtags','-') or '-')}  "
            f"**sync**: {row.get('sync_level','-')}  **时长**: {row.get('video_total_duration','-')}s")
    _v = str(row.get('vocal_presence','') or '').strip()
    voc = _v if _v in VOCAL_OPTS and _v != 'Unmarked' else 'Unmarked'
    sync_v = norm_sync(row.get('sync_level'))
    _gv = str(row.get('genre','') or '').strip()
    genre_v = '' if _gv in ('nan','') else _gv
    fp_v = str(row.get('full_song_path','') or '').strip()
    off  = _get_offset(row)
    em = set(parse_labels(row.get('emotion')))
    st = set(parse_labels(row.get('style')))
    sc = set(parse_labels(row.get('usage_scene')))
    # 22 + 11 (seg) = 33 values
    return (info, audio_html(audio_p), video_html(video_p), voc, sync_v, genre_v,
            verify_status_md(row), full_song_html(fp_v, off), off,
            pick(em,EMO_POS), pick(em,EMO_NEU), pick(em,EMO_NEG),
            pick(st,STY_PER), pick(st,STY_VIS), pick(st,STY_ATM),
            pick(st,STY_CUL), pick(st,STY_EDI), pick(st,STY_CON),
            pick(sc,SCE_DAI), pick(sc,SCE_SOC), pick(sc,SCE_CRE), pick(sc,SCE_SPE)
            ) + seg_outputs(row, sync_v)

# 26 字面量 + 10 个隐藏 radio = 36 = OUTPUTS 数
DONE_RETURN = ("全部完成",-1,[],"","","done","Unmarked","No","","done","",0,
               [],[],[],[],[],[],[],[],[],[],[],[],[],"") + _SEG_HIDDEN
ERR_RETURN  = ("no records",-1,[],"","","","Unmarked","No","","","",0,
               [],[],[],[],[],[],[],[],[],[],[],[],[],"") + _SEG_HIDDEN

# ── Gradio 界面 ────────────────────────────────────────────────────────────
with gr.Blocks(title="MGSV Annotation") as demo:
    state_idx   = gr.State(value=-1)
    state_queue = gr.State(value=[])

    gr.Markdown("# MGSV 人工标注工具")
    with gr.Row():
        progress_text = gr.Markdown("loading...")
        btn_reload    = gr.Button("Reload Excel", size="sm")

    with gr.Row():
        with gr.Column(scale=2):
            audio_box = gr.HTML(label="片段音频")
        with gr.Column(scale=3):
            video_box = gr.HTML(label="视频")

    info_box = gr.Markdown("info")

    with gr.Accordion("🔍 识曲核对", open=True):
        verify_md = gr.Markdown("识曲: 加载中...")
        with gr.Row():
            full_song_box  = gr.HTML(scale=4)
            with gr.Column(scale=1, min_width=130):
                offset_input = gr.Number(label="起播位置 (秒)", value=0, precision=1,
                                         minimum=0, step=1)
                btn_seek     = gr.Button("↩ 更新位置", size="sm")
        with gr.Row():
            btn_song_ok  = gr.Button("✅ 识曲正确，原曲已确认", size="sm", variant="primary")
            btn_song_bad = gr.Button("❌ 识曲有误（人工处理）",  size="sm", variant="secondary")

    with gr.Accordion("🔧 手动处理（原曲缺失 / 识曲有误时）", open=False):
        gr.Markdown("可填 QQ 链接；没有版权时也可粘贴“歌曲名/歌手名/专辑名”文本，或留空用当前歌名歌手搜索：")
        manual_url = gr.Textbox(
            label="QQ音乐链接 / song_mid（可选）",
            placeholder="可留空；或填 QQ 链接；或粘贴：歌曲名：...，歌手名：...，专辑名：...")
        with gr.Row():
            btn_manual_dl  = gr.Button("⬇ 下载/搜索原曲", size="sm", variant="primary")
            btn_delete_row = gr.Button("🗑 删除此条数据",     size="sm", variant="stop")
        manual_status = gr.Markdown("")

    with gr.Row():
        vocal_radio = gr.Radio(choices=VOCAL_OPTS, label="🎤 vocal_presence", value="Unmarked", scale=1)
        sync_radio  = gr.Radio(choices=['Yes','No'], label="Sync video",
                               value='No', scale=1)
        genre_box   = gr.Textbox(label="🎵 Genre", placeholder="Pop, Electronic, R&B ...", scale=2,
                                 interactive=True)

    with gr.Accordion("🎬 分镜段配乐契合度打分", open=True):
        seg_area = gr.HTML("")
        with gr.Row():
            btn_redetect_shots = gr.Button("重新检测当前分镜", size="sm", variant="secondary")
            shot_status = gr.Markdown("")
        with gr.Row():
            seg_a1 = gr.Radio(choices=SCORE_OPTS, label="A段1", visible=False, scale=1)
            seg_a2 = gr.Radio(choices=SCORE_OPTS, label="A段2", visible=False, scale=1)
            seg_a3 = gr.Radio(choices=SCORE_OPTS, label="A段3", visible=False, scale=1)
            seg_a4 = gr.Radio(choices=SCORE_OPTS, label="A段4", visible=False, scale=1)
        with gr.Row():
            seg_b1 = gr.Radio(choices=SCORE_OPTS, label="B段1", visible=False, scale=1)
            seg_b2 = gr.Radio(choices=SCORE_OPTS, label="B段2", visible=False, scale=1)
            seg_b3 = gr.Radio(choices=SCORE_OPTS, label="B段3", visible=False, scale=1)
        with gr.Row():
            seg_b4 = gr.Radio(choices=SCORE_OPTS, label="B段4", visible=False, scale=1)
            seg_b5 = gr.Radio(choices=SCORE_OPTS, label="B段5", visible=False, scale=1)
            seg_b6 = gr.Radio(choices=SCORE_OPTS, label="B段6", visible=False, scale=1)
        with gr.Row():
            seg_a5 = gr.Radio(choices=SCORE_OPTS, label="A段5", visible=False, scale=1)
            seg_a6 = gr.Radio(choices=SCORE_OPTS, label="A段6", visible=False, scale=1)
            seg_a7 = gr.Radio(choices=SCORE_OPTS, label="A段7", visible=False, scale=1)
            seg_a8 = gr.Radio(choices=SCORE_OPTS, label="A段8", visible=False, scale=1)
        with gr.Row():
            seg_a9 = gr.Radio(choices=SCORE_OPTS, label="A段9", visible=False, scale=1)
            seg_a10 = gr.Radio(choices=SCORE_OPTS, label="A段10", visible=False, scale=1)
            seg_a11 = gr.Radio(choices=SCORE_OPTS, label="A段11", visible=False, scale=1)
            seg_a12 = gr.Radio(choices=SCORE_OPTS, label="A段12", visible=False, scale=1)
        with gr.Row():
            seg_b7 = gr.Radio(choices=SCORE_OPTS, label="B段7", visible=False, scale=1)
            seg_b8 = gr.Radio(choices=SCORE_OPTS, label="B段8", visible=False, scale=1)
            seg_b9 = gr.Radio(choices=SCORE_OPTS, label="B段9", visible=False, scale=1)
        with gr.Row():
            seg_b10 = gr.Radio(choices=SCORE_OPTS, label="B段10", visible=False, scale=1)
            seg_b11 = gr.Radio(choices=SCORE_OPTS, label="B段11", visible=False, scale=1)
            seg_b12 = gr.Radio(choices=SCORE_OPTS, label="B段12", visible=False, scale=1)
    SEG_RADIOS = [seg_a1, seg_a2, seg_a3, seg_a4,
                  seg_a5, seg_a6, seg_a7, seg_a8, seg_a9, seg_a10, seg_a11, seg_a12,
                  seg_b1, seg_b2, seg_b3, seg_b4, seg_b5, seg_b6,
                  seg_b7, seg_b8, seg_b9, seg_b10, seg_b11, seg_b12]

    gr.Markdown("### 😊 Emotion — 情绪")
    with gr.Row():
        emo_pos = gr.CheckboxGroup(choices=EMO_POS, label="正向", scale=3)
        emo_neu = gr.CheckboxGroup(choices=EMO_NEU, label="中性", scale=2)
        emo_neg = gr.CheckboxGroup(choices=EMO_NEG, label="负向", scale=2)

    gr.Markdown("### 🎨 Style — 风格感")
    with gr.Row():
        sty_per = gr.CheckboxGroup(choices=STY_PER, label="人物向", scale=2)
        sty_vis = gr.CheckboxGroup(choices=STY_VIS, label="视觉向", scale=2)
        sty_atm = gr.CheckboxGroup(choices=STY_ATM, label="氛围向", scale=3)
    with gr.Row():
        sty_cul = gr.CheckboxGroup(choices=STY_CUL, label="文化向", scale=2)
        sty_edi = gr.CheckboxGroup(choices=STY_EDI, label="剪辑向", scale=3)
        sty_con = gr.CheckboxGroup(choices=STY_CON, label="内容向", scale=2)

    gr.Markdown("### 🎬 Usage Scene — 使用场景")
    with gr.Row():
        sce_dai = gr.CheckboxGroup(choices=SCE_DAI, label="日常生活", scale=3)
        sce_soc = gr.CheckboxGroup(choices=SCE_SOC, label="社交情感", scale=2)
    with gr.Row():
        sce_cre = gr.CheckboxGroup(choices=SCE_CRE, label="视频创作", scale=3)
        sce_spe = gr.CheckboxGroup(choices=SCE_SPE, label="特殊场景", scale=2)

    with gr.Row():
        btn_prev    = gr.Button("← 上一条",      size="sm")
        btn_skip    = gr.Button("跳过",           size="sm", variant="secondary")
        btn_save    = gr.Button("✅ 保存并继续 →", size="lg", variant="primary")
        btn_reannot = gr.Button("🔄 重新标注全部", size="sm", variant="secondary")

    OUTPUTS = [
        progress_text, state_idx, state_queue,
        audio_box, video_box, info_box,
        vocal_radio, sync_radio, genre_box,
        verify_md, full_song_box, offset_input,
        emo_pos, emo_neu, emo_neg,
        sty_per, sty_vis, sty_atm, sty_cul, sty_edi, sty_con,
        sce_dai, sce_soc, sce_cre, sce_spe,
        seg_area, *SEG_RADIOS,
    ]  # 36

    SAVE_INPUTS = [
        state_idx, state_queue, vocal_radio, sync_radio, genre_box,
        emo_pos, emo_neu, emo_neg,
        sty_per, sty_vis, sty_atm, sty_cul, sty_edi, sty_con,
        sce_dai, sce_soc, sce_cre, sce_spe,
        *SEG_RADIOS,
    ]  # 28

    def make_prog(df, queue):
        return f"**进度**: {len(df)-len(queue)}/{len(df)}  **待标注**: {len(queue)}"

    def _pack(df, queue, rec, idx=None):
        active_idx = idx if idx is not None else (queue[0] if queue else -1)
        if active_idx != -1:
            _write_last_idx(active_idx)
        return (make_prog(df, queue), active_idx, queue) + rec

    def init_tool():
        df = load_df()
        q  = get_unannotated_indices(df)
        if not q: return DONE_RETURN
        target = _resume_target(q)
        return _pack(df, q, load_record(df, target), target)

    def reannot_all():
        df = load_df()
        q  = df.index.tolist()
        return _pack(df, q, load_record(df, q[0]))

    def _save_current_form(df, cur_idx,
                           vocal, sync_v, genre_v,
                           ep, en, eg,
                           sp, sv, sa, sc, se, sco,
                           sd, ss, scr, ssp,
                           *seg_vals):
        old_vocal = _norm_vocal_value(df.loc[cur_idx].get('vocal_presence', ''))
        old_genre = str(df.loc[cur_idx].get('genre', '') or '').strip()
        vocal_v = _norm_vocal_value(vocal) or old_vocal
        if not vocal_v:
            vocal_v = _recover_vocal_from_backups(cur_idx)
        if not vocal_v:
            print(f"  vocal not saved: row {cur_idx}, input={vocal!r}, old={old_vocal!r}")
        genre_save = (genre_v or '').strip()
        if not genre_save and old_genre.lower() not in ('', 'nan', 'none', 'null'):
            genre_save = old_genre
        sync_norm = norm_sync(sync_v)
        emotion_v = format_labels((ep or[])+(en or[])+(eg or[]))
        style_v = format_labels((sp or[])+(sv or[])+(sa or[])+(sc or[])+(se or[])+(sco or[]))
        scene_v = format_labels((sd or[])+(ss or[])+(scr or[])+(ssp or[]))

        df.at[cur_idx,'vocal_presence'] = vocal_v
        df.at[cur_idx,'sync_level']     = sync_norm
        df.at[cur_idx,'genre']          = genre_save
        df.at[cur_idx,'emotion']        = emotion_v
        df.at[cur_idx,'style']          = style_v
        df.at[cur_idx,'usage_scene']    = scene_v
        boundsA, _, boundsB = get_schemes(df.loc[cur_idx], sync_norm)
        old_a_raw = str(df.loc[cur_idx].get('seg_scores_3') or '').strip()
        old_b_raw = str(df.loc[cur_idx].get('seg_scores_5') or '').strip()
        old_a_scores = parse_seg_scores(df.loc[cur_idx].get('seg_scores_3'))
        old_b_scores = parse_seg_scores(df.loc[cur_idx].get('seg_scores_5'))
        def _join(vals, n, old_scores):
            return '/'.join((str(vals[k]) if str(vals[k]) in SCORE_OPTS
                             else (old_scores[k] if k < len(old_scores) and old_scores[k] in SCORE_OPTS else '-'))
                            for k in range(n))
        a_vals = list(seg_vals[:SEG_A_SLOTS])
        b_vals = list(seg_vals[SEG_A_SLOTS:SEG_A_SLOTS + SEG_B_SLOTS])
        df.at[cur_idx,'seg_scores_3'] = _join(a_vals, min(len(boundsA), SEG_A_SLOTS), old_a_scores) if boundsA else old_a_raw
        df.at[cur_idx,'seg_scores_5'] = _join(b_vals, min(len(boundsB), SEG_B_SLOTS), old_b_scores) if boundsB else old_b_raw
        return _row_required_missing(df.loc[cur_idx])
        missing = []
        if not vocal_v:
            missing.append('vocal_presence')
        if not sync_norm:
            missing.append('Sync video')
        if not emotion_v:
            missing.append('Emotion')
        if not style_v:
            missing.append('Style')
        if not scene_v:
            missing.append('Usage Scene')
        if is_sync_yes(sync_norm) and not boundsA:
            missing.append('分镜检测')
        need_scores = min(len(boundsA), SEG_A_SLOTS) if boundsA else 0
        if need_scores and any(str(a_vals[k]) not in SCORE_OPTS for k in range(need_scores)):
            missing.append('分段评分')
        return missing

    def save_and_next(cur_idx, queue,
                      vocal, sync_v, genre_v,
                      ep, en, eg,
                      sp, sv, sa, sc, se, sco,
                      sd, ss, scr, ssp,
                      *seg_vals):
        if cur_idx == -1: return ERR_RETURN
        df = load_df()
        missing = _save_current_form(df, cur_idx, vocal, sync_v, genre_v,
                                     ep, en, eg, sp, sv, sa, sc, se, sco,
                                     sd, ss, scr, ssp, *seg_vals)
        save_df(df)
        if missing:
            nq = get_unannotated_indices(df)
            gr.Warning("Required fields missing: " + ", ".join(missing))
            return _pack(df, nq, load_record(df, cur_idx), cur_idx)
            gr.Warning("还有必填项未完成: " + ", ".join(missing))
            return _pack(df, nq, load_record(df, cur_idx), cur_idx)
        nq, target = _queue_after(df, cur_idx)
        if target == -1: return DONE_RETURN
        return _pack(df, nq, load_record(df, target), target)
        if missing:
            gr.Warning("还有必填项未完成：" + "、".join(missing))
            return _pack(df, queue or [], load_record(df, cur_idx), cur_idx)
        nq = [i for i in queue if i != cur_idx]
        if not nq: return DONE_RETURN
        return _pack(df, nq, load_record(df, nq[0]))

    def skip_record(cur_idx, queue,
                    vocal, sync_v, genre_v,
                    ep, en, eg,
                    sp, sv, sa, sc, se, sco,
                    sd, ss, scr, ssp,
                    *seg_vals):
        if cur_idx == -1: return ERR_RETURN
        df = load_df()
        _save_current_form(df, cur_idx, vocal, sync_v, genre_v,
                           ep, en, eg, sp, sv, sa, sc, se, sco,
                           sd, ss, scr, ssp, *seg_vals)
        save_df(df)
        q, target = _queue_after(df, cur_idx)
        if target == -1: return DONE_RETURN
        return _pack(df, q, load_record(df, target), target)

    def prev_record(cur_idx, queue,
                    vocal, sync_v, genre_v,
                    ep, en, eg,
                    sp, sv, sa, sc, se, sco,
                    sd, ss, scr, ssp,
                    *seg_vals):
        if cur_idx == -1: return ERR_RETURN
        df = load_df()
        _save_current_form(df, cur_idx, vocal, sync_v, genre_v,
                           ep, en, eg, sp, sv, sa, sc, se, sco,
                           sd, ss, scr, ssp, *seg_vals)
        save_df(df)
        queue = get_unannotated_indices(df)
        target = max(0, int(cur_idx) - 1)
        return _pack(df, queue or [], load_record(df, target), target)

    def update_vocal_only(cur_idx, vocal):
        if cur_idx is None or cur_idx == -1:
            return
        vocal_v = _norm_vocal_value(vocal)
        if not vocal_v:
            return
        df = load_df()
        df.at[int(cur_idx), 'vocal_presence'] = vocal_v
        save_df(df)
        print(f"  vocal saved: row {int(cur_idx)} -> {vocal_v}")

    def confirm_song(cur_idx):
        if cur_idx == -1: return gr.update()
        df = load_df()
        df.at[cur_idx, 'song_verified'] = '是'
        save_df(df)
        return gr.update(value=verify_status_md(df.loc[cur_idx]))

    def reject_song(cur_idx):
        if cur_idx == -1: return gr.update()
        df = load_df()
        df.at[cur_idx, 'song_verified']  = '否—有误'
        df.at[cur_idx, 'full_song_path'] = 'REJECTED'
        save_df(df)
        return gr.update(value=verify_status_md(df.loc[cur_idx]))

    def update_offset(cur_idx, offset):
        if cur_idx == -1: return gr.update()
        try: off = float(offset or 0)
        except: off = 0.0
        df = load_df()
        _set_song_span(df, cur_idx, off)
        save_df(df)
        fp = str(df.at[cur_idx, 'full_song_path'] or '').strip()
        return gr.update(value=full_song_html(fp, off))

    def manual_download(cur_idx, url):
        if cur_idx == -1:
            return gr.update(), gr.update(), gr.update(), gr.update(value="⚠ 当前没有记录")
        df  = load_df()
        row = df.loc[cur_idx]
        raw_url = (url or '').strip()
        parsed_song = _parse_manual_song_text(raw_url)
        if parsed_song.get('song_title'):
            df.at[cur_idx, 'song_title'] = parsed_song['song_title']
        if parsed_song.get('song_artist'):
            df.at[cur_idx, 'song_artist'] = parsed_song['song_artist']
        if raw_url and not parsed_song:
            qq_info = _qq_song_info_from_input(raw_url)
            if qq_info.get('song_title'):
                df.at[cur_idx, 'song_title'] = qq_info['song_title']
            if qq_info.get('song_artist'):
                df.at[cur_idx, 'song_artist'] = qq_info['song_artist']
            if qq_info.get('song_mid'):
                df.at[cur_idx, 'qq_song_mid'] = qq_info['song_mid']
            if qq_info:
                print(
                    "  QQ link metadata -> "
                    f"title={qq_info.get('song_title','')}, "
                    f"artist={qq_info.get('song_artist','')}, "
                    f"song_mid={qq_info.get('song_mid','')}"
                )
        row = df.loc[cur_idx]
        base = _manual_download_base(row, cur_idx, raw_url)
        fallback_msg = ""
        reused_existing = False
        reuse_rejected = False
        fp = _existing_exact_song_file(row)
        if fp:
            reused_existing = True
            fallback_msg = "复用同名已下载原曲"
        else:
            by_mid = _existing_song_by_qq_mid(row, df, cur_idx)
            if by_mid:
                fp = by_mid
                reused_existing = True
                fallback_msg = "复用相同 QQ song_mid 的已下载原曲"
            else:
                fp = None
        if not fp:
            fp = _existing_full_song(row, df, cur_idx)
            if fp:
                reused_existing = True
                fallback_msg = "复用同歌名歌手/元数据已下载原曲"
            elif raw_url and not parsed_song:
                fp = qdl(raw_url, base)
            else:
                fp = None
                fallback_msg = "按输入/当前歌名歌手搜索" if parsed_song else "未填写 QQ 链接，按歌名/歌手搜索"
        if not fp:
            fp, fallback_msg = _download_by_title_fallback(row, base)
        if not fp:
            return gr.update(), gr.update(), gr.update(), gr.update(
                value=f"❌ 下载失败：{fallback_msg or '请先确认已有歌名/歌手，或填写 QQ 链接'}")
        if reused_existing:
            meta_title, meta_artist = _audio_metadata_title_artist(fp)
        else:
            fp, meta_title, meta_artist = _rename_download_from_metadata(fp, cur_idx)
        meta_msg = ""
        if _clean_filename_part(meta_title):
            df.at[cur_idx, 'song_title'] = meta_title
            meta_msg += f"；标题={meta_title}"
        if _clean_filename_part(meta_artist):
            df.at[cur_idx, 'song_artist'] = meta_artist
            meta_msg += f"；歌手={meta_artist}"
        detected_genre = _detect_genre_from_audio(fp)
        if _clean_filename_part(detected_genre) and not _clean_filename_part(row.get('genre')):
            df.at[cur_idx, 'genre'] = detected_genre
            meta_msg += f"；Genre={detected_genre}"
        def _align_current(song_fp):
            row_now = df.loc[cur_idx]
            off_now = _get_offset(row_now)
            msg_now = ""
            score_now = None
            v_file = str(row_now.get('video_id','') or '')
            audio_p = resolve_music_path(str(row_now.get('full_music_path','') or ''))
            if audio_p is None or not os.path.exists(audio_p):
                audio_p = PATH_INDEX.get(v_file, (None, None))[1]
            if audio_p:
                try:
                    from music_fetch import MATCH_GOOD, MATCH_MIN
                    new_off, score, align_err = _verify_and_align_timeout(
                        song_fp, audio_p, row_now.get('music_start'), row_now.get('music_end')
                    )
                    score_now = score
                    if new_off is not None and score is not None:
                        df.at[cur_idx, 'match_score'] = score
                        if score >= MATCH_GOOD:
                            off_now = new_off
                            msg_now = f"；自动对齐成功 score={score:.2f}, offset={off_now:.1f}s"
                        elif score >= MATCH_MIN:
                            off_now = new_off
                            msg_now = f"；自动对齐一般 score={score:.2f}, offset={off_now:.1f}s，建议人工核对"
                        else:
                            msg_now = f"；自动对齐分数偏低 score={score:.2f}，保留原 offset，建议人工核对"
                    else:
                        msg_now = f"；{align_err or '自动对齐未得到结果'}，保留原 offset"
                except Exception as e:
                    msg_now = f"；自动对齐失败：{e}"
            else:
                msg_now = "；未找到片段音频，保留原 offset"
            return off_now, msg_now, score_now

        off, align_msg, align_score = _align_current(fp)
        if reused_existing and align_score is not None and align_score < 0.15:
            reuse_rejected = True
            fp = None
            fallback_msg = "复用已下载原曲但指纹不匹配，改为重新下载"
            if raw_url and not parsed_song:
                fp = qdl(raw_url, base)
            if not fp:
                fp, fallback_msg = _download_by_title_fallback(row, base)
            if not fp:
                return gr.update(), gr.update(), gr.update(), gr.update(
                    value="❌ 复用失败且重新下载失败：请粘贴更明确的 QQ 链接或手动确认歌曲")
            fp, meta_title, meta_artist = _rename_download_from_metadata(fp, cur_idx)
            meta_msg = ""
            if _clean_filename_part(meta_title):
                df.at[cur_idx, 'song_title'] = meta_title
                meta_msg += f"；标题={meta_title}"
            if _clean_filename_part(meta_artist):
                df.at[cur_idx, 'song_artist'] = meta_artist
                meta_msg += f"；歌手={meta_artist}"
            off, align_msg, align_score = _align_current(fp)
        df.at[cur_idx, 'full_song_path'] = fp
        _set_song_span(df, cur_idx, off)
        df.at[cur_idx, 'song_verified']  = ''
        save_df(df)
        fname = os.path.basename(fp)
        if fallback_msg:
            meta_msg += f"；来源={fallback_msg}"
        return (gr.update(value=verify_status_md(df.loc[cur_idx])),
                gr.update(value=full_song_html(fp, off)),
                gr.update(value=off),
                gr.update(value=f"✅ 下载成功: {fname}{meta_msg}{align_msg}；请核对后再确认"))

    _manual_download_impl = manual_download
    def manual_download(cur_idx, url):
        if not _LONG_TASK_LOCK.acquire(blocking=False):
            return (
                gr.update(),
                gr.update(),
                gr.update(),
                gr.update(value="已有下载任务在进行，请等当前歌曲下载/对齐完成后再点下一首")
            )
        try:
            return _manual_download_impl(cur_idx, url)
        finally:
            _LONG_TASK_LOCK.release()

    def delete_row(cur_idx, queue):
        if cur_idx == -1: return ERR_RETURN
        df = load_df()
        df = df.drop(index=cur_idx).reset_index(drop=True)
        save_df(df)
        nq = get_unannotated_indices(df)
        if len(df) == 0: return DONE_RETURN
        target = min(int(cur_idx), len(df) - 1)
        return _pack(df, nq, load_record(df, target), target)

    def sync_changed(cur_idx, sync_v):
        """切换是否卡点 → 分段打分区实时刷新（容错：出错时不动界面）"""
        try:
            if cur_idx is None or cur_idx == -1:
                return _SEG_NOOP
            df = load_df()
            sync_norm = norm_sync(sync_v)
            return seg_outputs(df.loc[int(cur_idx)], sync_norm)
        except Exception as e:
            print(f"sync_changed error: {e}")
            return _SEG_NOOP

    def redetect_current_shots(cur_idx, sync_v):
        if cur_idx is None or cur_idx == -1:
            return ("⚠ 当前没有记录",) + _SEG_NOOP
        try:
            env = os.environ.copy()
            env['PYTHONIOENCODING'] = 'utf-8'
            r = subprocess.run(
                [sys.executable, os.path.join(SCRIPT_DIR, 'shot_detect.py'),
                 '--row', str(int(cur_idx)), '--min-conf', '0.35'],
                capture_output=True, text=True, encoding='utf-8',
                errors='replace', timeout=300, env=env
            )
            df = load_df()
            seg = seg_outputs(df.loc[int(cur_idx)], sync_v)
            tail = (r.stdout or r.stderr or '').strip().splitlines()
            msg = tail[-1] if tail else '分镜检测完成'
            if r.returncode != 0:
                msg = f"⚠ 分镜检测失败：{msg}"
            else:
                msg = f"✅ {msg}"
            return (msg,) + seg
        except Exception as e:
            return (f"⚠ 分镜检测失败：{e}",) + _SEG_NOOP

    def redetect_current_shots(cur_idx, sync_v):
        if cur_idx is None or cur_idx == -1:
            return ("当前没有记录", gr.update()) + _SEG_NOOP
        if not _LONG_TASK_LOCK.acquire(blocking=False):
            return ("已有下载/分镜任务在进行，请等当前任务完成后再操作", gr.update()) + _SEG_NOOP
        try:
            env = os.environ.copy()
            env['PYTHONIOENCODING'] = 'utf-8'
            r = subprocess.run(
                [sys.executable, os.path.join(SCRIPT_DIR, 'shot_detect.py'),
                 '--row', str(int(cur_idx)), '--min-conf', '0.35'],
                capture_output=True, text=True, encoding='utf-8',
                errors='replace', timeout=300, env=env
            )
            df = load_df()
            df.at[int(cur_idx), 'sync_level'] = norm_sync(1)
            save_df(df)
            seg = seg_outputs(df.loc[int(cur_idx)], norm_sync(1))
            tail = (r.stdout or r.stderr or '').strip().splitlines()
            msg = tail[-1] if tail else '分镜检测完成'
            msg = f"分镜检测失败: {msg}" if r.returncode != 0 else f"已重新检测分镜: {msg}"
            return (msg, gr.update(value=norm_sync(1))) + seg
        except Exception as e:
            return (f"分镜检测失败: {e}", gr.update()) + _SEG_NOOP
        finally:
            _LONG_TASK_LOCK.release()

    demo.load(fn=init_tool, outputs=OUTPUTS).then(
        fn=sync_changed, inputs=[state_idx, sync_radio], outputs=[seg_area, *SEG_RADIOS])
    btn_reload.click(fn=init_tool, outputs=OUTPUTS).then(
        fn=sync_changed, inputs=[state_idx, sync_radio], outputs=[seg_area, *SEG_RADIOS])
    btn_save.click(fn=save_and_next, inputs=SAVE_INPUTS, outputs=OUTPUTS).then(
        fn=sync_changed, inputs=[state_idx, sync_radio], outputs=[seg_area, *SEG_RADIOS])
    btn_skip.click(fn=skip_record, inputs=SAVE_INPUTS, outputs=OUTPUTS).then(
        fn=sync_changed, inputs=[state_idx, sync_radio], outputs=[seg_area, *SEG_RADIOS])
    btn_prev.click(fn=prev_record, inputs=SAVE_INPUTS, outputs=OUTPUTS).then(
        fn=sync_changed, inputs=[state_idx, sync_radio], outputs=[seg_area, *SEG_RADIOS])
    btn_reannot.click(fn=reannot_all, outputs=OUTPUTS).then(
        fn=sync_changed, inputs=[state_idx, sync_radio], outputs=[seg_area, *SEG_RADIOS])
    btn_song_ok.click(fn=confirm_song,   inputs=[state_idx], outputs=[verify_md])
    btn_song_bad.click(fn=reject_song,  inputs=[state_idx], outputs=[verify_md])
    btn_seek.click(fn=update_offset,
                   inputs=[state_idx, offset_input],
                   outputs=[full_song_box])
    btn_manual_dl.click(fn=manual_download,
                        inputs=[state_idx, manual_url],
                        outputs=[verify_md, full_song_box, offset_input, manual_status])
    btn_delete_row.click(fn=delete_row,
                         inputs=[state_idx, state_queue],
                         outputs=OUTPUTS).then(
        fn=sync_changed, inputs=[state_idx, sync_radio], outputs=[seg_area, *SEG_RADIOS])
    btn_redetect_shots.click(fn=redetect_current_shots,
                             inputs=[state_idx, sync_radio],
                             outputs=[shot_status, sync_radio, seg_area, *SEG_RADIOS])
    vocal_radio.change(fn=update_vocal_only,
                       inputs=[state_idx, vocal_radio],
                       outputs=[])
    sync_radio.change(fn=sync_changed,
                      inputs=[state_idx, sync_radio],
                      outputs=[seg_area, *SEG_RADIOS])

if __name__ == "__main__":
    demo.launch(server_name="127.0.0.1", server_port=7860, inbrowser=True,
                theme=gr.themes.Soft())
