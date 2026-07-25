import os
import cv2
import sys
import numpy as np
import pandas as pd
from scipy.signal import find_peaks
import matplotlib.pyplot as plt
import warnings

# ==================== 【静音与显示修复区】 ====================
# 1. 屏蔽 Pandas 的类型强迫症警告
warnings.filterwarnings("ignore", category=FutureWarning)

# 2. 彻底解决 Matplotlib 图表中文显示乱码/报错的问题
# 优先使用 Windows 自带的黑体 (SimHei) 或 微软雅黑 (Microsoft YaHei)
plt.rcParams['font.sans-serif'] = ['SimHei', 'Microsoft YaHei', 'sans-serif']
plt.rcParams['axes.unicode_minus'] = False  # 保证坐标轴负号正常显示
# ==============================================================



def calculate_upward_midpoints(lows, highs):
    """自动配对时间顺序上的 (低点, 高点)，并计算向上运动的中间发力点"""
    visual_beats = []
    for l_time in lows:
        valid_highs = highs[highs > l_time]
        if len(valid_highs) > 0:
            next_h_time = valid_highs[0]
            midpoint = round(float((l_time + next_h_time) / 2.0), 3)
            visual_beats.append(midpoint)
    return visual_beats

def track_and_find_peaks(video_path, excel_path="./outputs/MGSV_Master_Dataset.xlsx"):
    """
    半自动目标追踪卡点器 + 自动反填 Excel 总表
    """
    cap = cv2.VideoCapture(video_path)
    fps = cap.get(cv2.CAP_PROP_FPS)
    if fps <= 0: fps = 30.0

    ret, frame = cap.read()
    if not ret:
        print("❌ 无法读取视频")
        return

    print("👉 请在弹出的窗口中，用鼠标框选出【喷井的金属杆头部】。")
    print("👉 框选完成后，按 【空格键】 或 【Enter键】 开始追踪。")
    
    bbox = cv2.selectROI("Select Target", frame, fromCenter=False, showCrosshair=True)
    cv2.destroyWindow("Select Target")

    # 初始化 OpenCV 追踪器（全版本兼容写法）
    try:
        tracker = cv2.TrackerCSRT_create()
    except AttributeError:
        try:
            tracker = cv2.legacy.TrackerCSRT_create()
        except AttributeError:
            print("❌ 严重环境错误：找不到 CSRT 追踪器！")
            return

    tracker.init(frame, bbox)
    y_centers = []
    
    print("🚀 正在追踪目标 Y 坐标轨迹，请稍候...")
    while True:
        ret, frame = cap.read()
        if not ret: break
        
        success, bbox = tracker.update(frame)
        if success:
            center_y = bbox[1] + bbox[3] / 2.0
            y_centers.append(center_y)
        else:
            y_centers.append(y_centers[-1] if len(y_centers) > 0 else 0)

    cap.release()
    
    # 轨迹平滑与波峰探测
    y_centers = np.array(y_centers)
    from scipy.ndimage import gaussian_filter1d
    y_centers_smoothed = gaussian_filter1d(y_centers, sigma=2)
    
    peaks_high, _ = find_peaks(-y_centers_smoothed, distance=int(fps*0.5), prominence=2)
    peaks_low, _ = find_peaks(y_centers_smoothed, distance=int(fps*0.5), prominence=2)
    
    high_times = np.array([round(p / fps, 3) for p in peaks_high])
    low_times = np.array([round(p / fps, 3) for p in peaks_low])
    
    # 计算精调后的视觉发力卡点
    actual_visual_beats = calculate_upward_midpoints(low_times, high_times)
    
    print("-" * 50)
    print(f"✅ 机器追踪完成！")
    print(f"🎯 计算出的精准视觉卡点: {actual_visual_beats}")
    
    # =========================================================
    # 🛡️ 核心黑科技：智能融合并反填回 auto_rhythm_points_visual
    # =========================================================
    import ast # 引入安全字符串解析模块
    
    video_file_name = os.path.basename(video_path) # 获取文件名作为 ID
    
    if os.path.exists(excel_path):
        print(f"\n📂 正在打开总表 {excel_path} 进行智能融合反填...")
        df = pd.read_excel(excel_path)
        
        if 'video_id' in df.columns:
            df['video_id'] = df['video_id'].astype(str)
            match_condition = df['video_id'] == video_file_name
            
            if match_condition.any():
                # 1. 安全读取原来的 auto_rhythm_points_visual 列表
                existing_auto_str = df.loc[match_condition, 'auto_rhythm_points_visual'].values[0]
                try:
                    # 将字符串 "[1.2, 3.4]" 安全转化为 Python 列表
                    existing_auto = ast.literal_eval(existing_auto_str) if pd.notna(existing_auto_str) else []
                    if not isinstance(existing_auto, list):
                        existing_auto = []
                except:
                    existing_auto = []
                
                # 2. 核心融合：将原有的转场点与 CSRT 提取的动作点合并，去重，并按时间排序
                merged_visual_beats = sorted(list(set(existing_auto + actual_visual_beats)))
                
                # 3. 覆盖写入回 auto_rhythm_points_visual
                df.loc[match_condition, 'auto_rhythm_points_visual'] = str(merged_visual_beats)
                
                df.to_excel(excel_path, index=False)
                print(f"✍️  [融合成功] 已将 CSRT 结果与原视觉卡点完美合并，并覆写至 【{video_file_name}】！")
                print(f"📊  合并后的完整视觉卡点为: {merged_visual_beats}")
            else:
                print(f"⚠️  [未找到匹配] 总表中没有找到文件名为 【{video_file_name}】 的行。")
        else:
            print("❌ 总表中缺失 'video_id' 列，无法反填。")
    else:
        print(f"❌ 未找到总表文件 {excel_path}。")
    print("-" * 50)

    # 绘图展示
    time_axis = np.arange(len(y_centers)) / fps
    plt.figure(figsize=(10, 3))
    plt.plot(time_axis, -y_centers_smoothed, label="Y-Trajectory")
    plt.title(f"Tracked Motion: {video_file_name}")
    plt.grid(True)
    plt.show()

# 运行测试
# video_target = r"你的视频路径..."
# track_and_find_peaks(video_target)
# 运行测试
video_target = r"E:/Download/Douyin-Downloader/_internal/Volume/Download/2022-05-10 17.49.49-视频-东尚摄影黄老板-这么多好看的晚霞 当然是分享给你...行大玩家 #治愈系风景 #一起看日落/2022-05-10 17.49.49-视频-东尚摄影黄老板-这么多好看的晚霞 当然是分享给你...行大玩家 #治愈系风景 #一起看日落.mp4"
track_and_find_peaks(video_target)