import cv2
import numpy as np
import os

# ==================== 1. 配置区 ====================
# 填入你想要测试的那一张切片图片（最好是20倍下带阳性组织的局部图）
IMAGE_PATH = r"C:\Users\pc\Downloads\WSI_AOI_Positive\F4F3D5-NOX4_20.0x.jpg"
OUTPUT_PATH = r"C:\Users\pc\Downloads\WSI_AOI_Positive\Output_All\vehicle-2-青年肺 D3 2026.6.12-P-MLKL - 2026-07-09 17.28.56\1_raw_tiles\debug_overlay.png" # 可选：保存当前截图
# ==================================================

def imread_unicode(path):
    """支持中文路径的读取"""
    stream = np.fromfile(path, dtype=np.uint8)
    img = cv2.imdecode(stream, cv2.IMREAD_COLOR)
    if img is None:
        raise FileNotFoundError(f"找不到图片，请检查路径：{path}")
    return img

def empty(a):
    pass

# 创建窗口和滑动条
cv2.namedWindow("HSV Tuner")
cv2.resizeWindow("HSV Tuner", 800, 600)

# H: 0-179 (OpenCV标准), S: 0-255, V: 0-255
cv2.createTrackbar("H Min", "HSV Tuner", 0, 179, empty)
cv2.createTrackbar("H Max", "HSV Tuner", 179, 179, empty)
cv2.createTrackbar("S Min", "HSV Tuner", 0, 255, empty)
cv2.createTrackbar("S Max", "HSV Tuner", 255, 255, empty)
cv2.createTrackbar("V Min", "HSV Tuner", 0, 255, empty)
cv2.createTrackbar("V Max", "HSV Tuner", 255, 255, empty)

# 读取图像
img_bgr = imread_unicode(IMAGE_PATH)
img_hsv = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2HSV)

while True:
    # 获取当前滑块的值
    h_min = cv2.getTrackbarPos("H Min", "HSV Tuner")
    h_max = cv2.getTrackbarPos("H Max", "HSV Tuner")
    s_min = cv2.getTrackbarPos("S Min", "HSV Tuner")
    s_max = cv2.getTrackbarPos("S Max", "HSV Tuner")
    v_min = cv2.getTrackbarPos("V Min", "HSV Tuner")
    v_max = cv2.getTrackbarPos("V Max", "HSV Tuner")

    # 防止最小值大于最大值
    if h_min > h_max: h_max = h_min
    if s_min > s_max: s_max = s_min
    if v_min > v_max: v_max = v_min

    # 生成掩膜
    lower = np.array([h_min, s_min, v_min])
    upper = np.array([h_max, s_max, v_max])
    mask = cv2.inRange(img_hsv, lower, upper)

    # 生成实时叠加效果（选择区域标记为亮绿色半透明）
    overlay = img_bgr.copy()
    # 将掩膜区域变成亮绿色 (BGR: 0, 255, 0)
    overlay[mask > 0] = [0, 255, 0]

    # 融合原图和叠加图，透明度0.5
    final_display = cv2.addWeighted(img_bgr, 0.5, overlay, 0.5, 0)

    # 显示
    cv2.imshow("HSV Tuner", final_display)

    key = cv2.waitKey(1) & 0xFF
    
    # 按 'q' 退出
    if key == ord('q') or key == 27:
        break
    
    # 按 's' 保存当前截图到本地
    if key == ord('s'):
        cv2.imwrite(OUTPUT_PATH, final_display)
        print(f"💾 已保存当前截图到: {OUTPUT_PATH}")

    # 按 'p' 打印当前参数到控制台
    if key == ord('p'):
        print("-----------------------------------")
        print("当前调试出的HSV参数：")
        print(f"H: {h_min} ~ {h_max}")
        print(f"S: {s_min} ~ {s_max}")
        print(f"V: {v_min} ~ {v_max}")
        print("-----------------------------------")
        # 强烈建议：同时打印对应这个参数的RGB辅助逻辑
        b, g, r = cv2.split(img_bgr)
        print("（辅助判定）加入 r > b 可排除蓝色细胞核：", "True" if r.mean() > b.mean() else "False")

cv2.destroyAllWindows()