import os
import json
import time
import subprocess
import warnings
import pandas as pd

warnings.filterwarnings("ignore")

# ==================== 【配置区域】 ====================

EXTERNAL_DATA_ROOT = r"E:/Download/Douyin-Downloader/_internal/Volume/Download"
OUTPUT_DIR         = "./outputs"
FULL_MUSIC_DIR     = "./outputs/full_music"   # 完整原曲统一存放目录（同名歌曲只下一次）
TRACKING_EXCEL     = os.path.join(OUTPUT_DIR, "acrcloud_tracking.xlsx")

os.makedirs(OUTPUT_DIR,     exist_ok=True)
os.makedirs(FULL_MUSIC_DIR, exist_ok=True)

# ACRCloud 配置（https://console.acrcloud.com 注册免费开发者账号获取）
ACRCLOUD_CONFIG = {
    'host':          'identify-ap-southeast-1.acrcloud.com',
    'access_key':    '3a6d2c888ccb867a556bc3baf58b9ac9',
    'access_secret': 'UwN90N9KVv3GPNSeZUBtPPviLTYOvIEuKCUxOEuJ',
    'timeout':       10,
}

# 识曲置信度阈值（0~100），低于此值视为失败，跳过下载
ACR_CONFIDENCE_THRESHOLD = 75


# ==================== 【Step 1: ACRCloud 听歌识曲】 ====================

def recognize_music(video_path, start_seconds=5):
    """
    直接调用 ACRCloud REST API（不依赖 SDK），
    返回 (title, artist, confidence)，失败返回 (None, None, 0)。

    start_seconds: 从第几秒开始取样（跳过片头字幕/无声段）
    """
    try:
        import base64
        import hashlib
        import hmac
        import requests
        import librosa
        import numpy as np

        host        = ACRCLOUD_CONFIG['host']
        access_key  = ACRCLOUD_CONFIG['access_key']
        access_secret = ACRCLOUD_CONFIG['access_secret']

        import io
        import scipy.io.wavfile as wav_io

        # 1. 用 librosa 从视频中提取 20 秒音频（8kHz 单声道，ACRCloud 要求）
        y, sr = librosa.load(video_path, sr=8000, offset=start_seconds,
                             duration=20, mono=True)
        pcm = (y * 32767).astype(np.int16)

        # 2. 封装成带 WAV 头的完整文件（ACRCloud 不接受裸 PCM）
        buf = io.BytesIO()
        wav_io.write(buf, sr, pcm)
        audio_bytes = buf.getvalue()

        # 2. 构造 HMAC-SHA1 签名
        http_method = 'POST'
        uri         = '/v1/identify'
        data_type   = 'audio'
        signature_version = '1'
        timestamp   = str(int(time.time()))

        string_to_sign = '\n'.join([http_method, uri, access_key,
                                    data_type, signature_version, timestamp])
        sign = base64.b64encode(
            hmac.new(access_secret.encode('utf-8'),
                     string_to_sign.encode('utf-8'),
                     hashlib.sha1).digest()
        ).decode('utf-8')

        # 3. POST 请求
        files  = {'sample': ('audio.wav', audio_bytes, 'audio/wav')}
        data   = {
            'access_key':        access_key,
            'data_type':         data_type,
            'signature_version': signature_version,
            'signature':         sign,
            'sample_bytes':      len(audio_bytes),
            'timestamp':         timestamp,
        }
        url    = f"https://{host}{uri}"
        resp   = requests.post(url, files=files, data=data,
                               timeout=ACRCLOUD_CONFIG.get('timeout', 10))
        result = resp.json()

        code = result.get('status', {}).get('code', -1)
        if code != 0:
            msg = result.get('status', {}).get('msg', '')
            print(f"  ⚠️ ACRCloud 返回 code={code}: {msg}")
            return None, None, 0

        music_list = result.get('metadata', {}).get('music', [])
        if not music_list:
            return None, None, 0

        top        = music_list[0]
        title      = top.get('title', '').strip()
        artists    = top.get('artists', [{}])
        artist     = artists[0].get('name', '').strip() if artists else ''
        confidence = int(top.get('score', 0))

        return title, artist, confidence

    except Exception as e:
        print(f"  ❌ ACRCloud 识曲异常: {e}")
        return None, None, 0


# ==================== 【Step 2: yt-dlp 下载完整原曲】 ====================

def download_full_music(title, artist, output_path):
    """
    用 yt-dlp 在 YouTube 搜索 "{title} {artist} official audio" 并下载为 m4a。
    如果文件已存在（同名歌曲被另一个视频触发过）则直接返回，不重复下载。
    返回实际保存路径，失败返回 None。
    """
    if os.path.exists(output_path):
        return output_path  # 已存在，不重复下载

    # 三级 fallback：YouTube official audio → YouTube 普通 → B站搜索（华语歌备用）
    artist_part = f" {artist}" if artist else ""
    queries = [
        f"ytsearch1:{title}{artist_part} official audio",
        f"ytsearch1:{title}{artist_part}",
        f"bilisearch1:{title}{artist_part}",   # B站兜底，对华语小众曲命中率高
    ]

    for query in queries:
        temp_template = output_path.replace('.m4a', '.%(ext)s')
        cmd = [
            'yt-dlp',
            '-x',
            '--audio-format', 'm4a',
            '--audio-quality', '0',
            '--no-playlist',
            '--match-filter', 'duration > 60',   # 过滤掉明显是短片/广告的结果
            '-o', temp_template,
            '--quiet',
            '--no-warnings',
            query,
        ]
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=180)

            # yt-dlp 有时会输出 .webm/.opus，检查所有可能的扩展名
            if os.path.exists(output_path):
                return output_path
            for ext in ['webm', 'opus', 'mp3']:
                alt = output_path.replace('.m4a', f'.{ext}')
                if os.path.exists(alt):
                    return alt

        except subprocess.TimeoutExpired:
            print("  ⚠️ yt-dlp 下载超时，跳过此查询")
            continue
        except FileNotFoundError:
            print("  ❌ 未找到 yt-dlp：pip install yt-dlp")
            return None
        except Exception as e:
            print(f"  ❌ yt-dlp 异常: {e}")
            continue

    return None


# ==================== 【主流程】 ====================

def main():
    print(f"正在扫描目录: {EXTERNAL_DATA_ROOT}")

    # ---- 增量更新：读取已处理记录，节省 ACRCloud 每日配额 ----
    if os.path.exists(TRACKING_EXCEL):
        tracking_df = pd.read_excel(TRACKING_EXCEL)
        processed   = set(tracking_df['video_id'].dropna().astype(str))
        print(f"📦 已有 {len(processed)} 条历史记录，将跳过重复处理")
    else:
        tracking_df = pd.DataFrame()
        processed   = set()
        print("🌱 首次运行，开始全量处理")

    print("-" * 50)
    new_records = []
    api_call_count = 0   # 本次运行 ACRCloud 调用计数（免费版每天 100 次）

    for root, dirs, files in os.walk(EXTERNAL_DATA_ROOT):
        video_files = [f for f in files if f.lower().endswith(('.mp4', '.avi', '.mov'))]
        if not video_files:
            continue

        v_file     = video_files[0]
        video_path = os.path.join(root, v_file)

        if v_file in processed:
            continue

        print(f"\n🎬 [{api_call_count + 1}] 处理: {v_file}")

        # ---- Step 1: 识曲 ----
        print("  🔍 ACRCloud 识曲中...")
        title, artist, confidence = recognize_music(video_path, start_seconds=5)
        api_call_count += 1

        if not title or confidence < ACR_CONFIDENCE_THRESHOLD:
            print(f"  ⚠️ 识曲失败或置信度不足（{confidence}/100），跳过下载")
            new_records.append({
                'video_id':        v_file,
                'video_path':      video_path,
                'song_title':      title or '',
                'song_artist':     artist or '',
                'acr_confidence':  confidence,
                'full_music_path': '',
                'status':          'recognition_failed',
            })
            time.sleep(1)
            continue

        print(f"  ✅ 识曲成功: {title} — {artist}（置信度 {confidence}/100）")

        # ---- Step 2: 下载完整原曲 ----
        # 统一存到 FULL_MUSIC_DIR，同一首歌多个视频共用同一个文件
        safe_name   = f"{title}_{artist}".replace('/', '_').replace('\\', '_') \
                                         .replace(' ', '_').replace('?', '').replace('*', '')
        output_path = os.path.join(FULL_MUSIC_DIR, f"{safe_name}.m4a")

        if os.path.exists(output_path):
            print(f"  📁 完整原曲已存在，复用: {os.path.basename(output_path)}")
            full_path = output_path
            status    = 'reused'
        else:
            print(f"  📥 YouTube 下载: {title} — {artist}")
            full_path = download_full_music(title, artist, output_path)
            if full_path:
                print(f"  ✅ 下载完成: {os.path.basename(full_path)}")
                status = 'downloaded'
            else:
                print(f"  ❌ YouTube 未找到或下载失败")
                status = 'download_failed'

        new_records.append({
            'video_id':        v_file,
            'video_path':      video_path,
            'song_title':      title,
            'song_artist':     artist,
            'acr_confidence':  confidence,
            'full_music_path': full_path or '',
            'status':          status,
        })

        # ACRCloud 免费版限速保护（建议相邻两次请求间隔 ≥ 2s）
        time.sleep(2)

    # ---- 保存追踪表 ----
    if new_records:
        new_df = pd.DataFrame(new_records)
        final_df = pd.concat([tracking_df, new_df], ignore_index=True) \
                   if not tracking_df.empty else new_df
        final_df.to_excel(TRACKING_EXCEL, index=False)
        success = sum(1 for r in new_records if r['status'] in ('downloaded', 'reused'))
        print(f"\n🎉 本次处理 {len(new_records)} 条，成功获取完整原曲 {success} 条")
        print(f"📊 追踪表已更新: {TRACKING_EXCEL}")
    else:
        print("\n☕ 没有新视频需要处理")


if __name__ == "__main__":
    main()
