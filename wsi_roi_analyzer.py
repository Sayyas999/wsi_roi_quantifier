import os
import json
import math
import cv2
import numpy as np
import pandas as pd
import openpyxl
import openslide
from shapely.geometry import shape, box
import staintools
from staintools import StainNormalizer
from skimage.color import separate_stains, combine_stains

# ======================== 1. 用户配置区 ========================
# 输入文件夹/文件
EXCEL_PATH = r"C:\Users\pc\Downloads\WSI_AOI_Positive\input_slides.xlsx"  # 包含 WSI 和 Annotation 路径的 Excel
OUTPUT_BASE_FOLDER = r"C:\Users\pc\Downloads\WSI_AOI_Positive\Output_20260916" # 总输出文件夹，每个切片在里面建一个文件夹

SLIDE_EXTENSIONS = ("*.ndpi", "*.svs", "*.mrxs", "*.tif", "*.tiff")  # 未来改成“自动扫描文件夹”模式

# ⚠️ 独立开关（按需开启/关闭）：
# 1. 是否保存原始切图 (1_raw_tiles)
SAVE_RAW_TILES_ENABLED = True    
# 2. 是否执行归一化算法 (整个过程)
NORMALIZATION_ENABLED = False      
# 3. 是否保存归一化后的图片 (2_normalized_tiles)
SAVE_NORMALIZED_TILES_ENABLED = False  # True的话，应该需要归一化算法功能开启
# 4. 是否保存伪彩标记图 (3_overlay_tiles)
SAVE_OVERLAY_TILES_ENABLED = True
# 5. 是否保存“全组织区域伪彩图”
SAVE_TISSUE_OVERLAY_ENABLED = True

STAIN_TYPE = 'IHC-Brown'      # 可选: 'HE', 'Masson', 'Movat', 'Tissue', 'IHC-Yellow', 'IHC-Brown', 'IHC-Nuclei'
REF_IMG_PATH = r"D:\Masson_2X\reference.jpg"

# 界定切片分析的具体放大倍率（脚本会自动推导MPP）
TARGET_MAGNIFICATION = 20      # 可选：10（粗筛）, 20（精细定量）, 5（快速预览） 等

# 自动推导目标 MPP (10/20=0.5, 10/10=1.0, 10/40=0.25)
TARGET_MPP = 10.0 / TARGET_MAGNIFICATION

TILE_SIZE = 2048

# ⚠️ 核心开关：只归一化，不分析
ANALYSIS_ENABLED = True   # 设为 False 时，只切图+归一化，直接跳过分析、伪彩和Excel导出

# 阳性阈值 (仅对 HE/Masson 生效，IHC 使用 HSV)
POS_B_THRESH, POS_R_THRESH, POS_G_THRESH = 130, 120, 140
ORG_BRIGHT_THRESH = 215
ORG_DARK_THRESH = 20

# Movat 五色染色手动 OD 向量
MOVAT_STAIN_MATRIX = np.array([
    [0.56, 0.66, 0.50], [0.72, 0.44, 0.53], [0.20, 0.75, 0.62],
    [0.10, 0.10, 0.99], [0.65, 0.30, 0.70]
])
# ======================================================================

# ----------------- 工具函数 -----------------
def imread_unicode(path):
    stream = np.fromfile(path, dtype=np.uint8)
    img = cv2.imdecode(stream, cv2.IMREAD_COLOR)
    return img

def imwrite_unicode(path, img_bgr):
    ext = os.path.splitext(path)[1].lower()
    if ext in ['.jpg', '.jpeg']:
        ret, img_encoded = cv2.imencode('.jpg', img_bgr, [cv2.IMWRITE_JPEG_QUALITY, 95])
    elif ext == '.png':
        ret, img_encoded = cv2.imencode('.png', img_bgr)
    elif ext in ['.tiff', '.tif']:
        ret, img_encoded = cv2.imencode('.tiff', img_bgr)
    else:
        ret, img_encoded = cv2.imencode('.jpg', img_bgr)
    if ret:
        img_encoded.tofile(path)

def load_polygons_from_geojson(geojson_path):
    with open(geojson_path, 'r', encoding='utf-8') as f:
        data = json.load(f)
    polygons, feature_names = [], []
    for idx, feature in enumerate(data.get('features', [])):
        geometry = feature.get('geometry', {})
        if geometry:
            polygons.append(shape(geometry))
            feature_names.append(feature.get('properties', {}).get('name', f"ROI_{idx}"))
    return polygons, feature_names

def preprocess_for_normalization(img_rgb, black_thresh=20, white_thresh=215):
    gray = cv2.cvtColor(img_rgb, cv2.COLOR_RGB2GRAY)
    mask_black = gray < black_thresh
    mask_white = gray > white_thresh
    img_for_calc = img_rgb.copy()
    img_for_calc[mask_black] = [255, 255, 255]
    img_for_calc[mask_white] = [255, 255, 255]
    return img_for_calc, mask_black, mask_white

import xml.etree.ElementTree as ET
from shapely.geometry import Polygon

def load_polygons_from_3dhistech_xml(xml_path):
    """
    解析 3DHISTECH SlideMaster 导出的 Annotation XML 文件。
    坐标使用最高倍率（Level 0）下的绝对像素坐标。
    """
    tree = ET.parse(xml_path)
    root = tree.getroot()
    
    polygons = []
    feature_names = []
    
    # 导航至 annotations 节点
    destination = root.find('destination')
    annotations = destination.find('annotations') if destination is not None else root.find('annotations')
    
    if annotations is None:
        raise ValueError(f"未在 XML 中找到 annotations 节点: {xml_path}")
        
    for annotation in annotations.findall('annotation'):
        name = annotation.get('name', f"ROI_{len(polygons)}")
        points = []
        for p in annotation.findall('p'):
            # 提取 x 和 y 属性
            x = float(p.get('x'))
            y = float(p.get('y'))
            points.append((x, y))
        
        if len(points) >= 3:
            polygons.append(Polygon(points))
            feature_names.append(name)
            
    return polygons, feature_names

# ----------------- IHC 阳性面积判定函数（HSV） -----------------
def get_ihc_positive_mask(img_bgr, stain_variant):
    hsv = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2HSV)
    h, s, v = cv2.split(hsv)
    b, g, r = cv2.split(img_bgr)
    
    if stain_variant == 'IHC-Brown':
        mask = (h >= 0) & (h <= 45) & (s > 4) & (v > 0) & (v < 200) & (r > b)
    elif stain_variant == 'IHC-Yellow':
        mask = (h >= 5) & (h <= 20) & (s > 4) & (v > 0) & (v < 200) & (r > b)
    elif stain_variant == 'IHC-Nuclei':
        mask = (h >= 140) & (h <= 179) & (s > 70) & (v > 0) & (v < 200) & (r > b)
    else:
        mask = np.zeros_like(h, dtype=np.uint8)
    return mask.astype(np.uint8) * 255

# ----------------- 归一化构建与应用 -----------------
def prepare_normalizer(ref_path, stain_type):
    if not NORMALIZATION_ENABLED:
        return None, None
    if stain_type in ['HE', 'Masson']:
        ref_bgr = imread_unicode(ref_path)
        ref_img = cv2.cvtColor(ref_bgr, cv2.COLOR_BGR2RGB)
        ref_for_calc, _, _ = preprocess_for_normalization(ref_img)
        normalizer = StainNormalizer(method='macenko')
        normalizer.fit(ref_for_calc)
        return normalizer, None
    elif stain_type == 'Movat':
        return None, MOVAT_STAIN_MATRIX
    else:
        return None, None

def apply_normalization(img_rgb, normalizer, stain_matrix, stain_type):
    img_for_calc, mask_black, mask_white = preprocess_for_normalization(img_rgb)
    if stain_type in ['HE', 'Masson']:
        normalized = normalizer.transform(img_for_calc)
    elif stain_type == 'Movat':
        od_img = rgb2od(img_for_calc)
        concentrations = separate_stains(od_img, stain_matrix)
        conc_max = np.max(concentrations, axis=(0,1), keepdims=True) + 1e-6
        concentrations = concentrations / conc_max
        normalized_od = combine_stains(concentrations, stain_matrix)
        normalized = od2rgb(normalized_od)
        normalized = (np.clip(normalized, 0, 1) * 255).astype(np.uint8)
    else:
        normalized = img_for_calc
    final_output = normalized.copy()
    final_output[mask_black] = [0, 0, 0]
    final_output[mask_white] = [255, 255, 255]
    return final_output

def rgb2od(img):
    with np.errstate(divide='ignore', invalid='ignore'):
        od = -np.log10((img.astype(np.float32) + 1) / 255.0)
    return od

def od2rgb(od):
    od = np.clip(od, 0, None)
    rgb = 255 * np.exp(-od)
    return np.clip(rgb, 0, 255).astype(np.uint8)

# ----------------- 第一步：切图 -----------------
def step1_extract_tiles(slide, polygon, output_dir, tile_size=TILE_SIZE):
    # 【新增修复】清理多边形拓扑，防止报错
    if not polygon.is_valid:
        polygon = polygon.buffer(0)
    polygon = polygon.buffer(0) # 强制清理（更稳妥）

    minx, miny, maxx, maxy = polygon.bounds
    native_mpp = float(slide.properties.get(openslide.PROPERTY_NAME_MPP_X, 0.25))
    downsample_factor = TARGET_MPP / native_mpp
    best_level = slide.get_best_level_for_downsample(downsample_factor)
    actual_downsample = slide.level_downsamples[best_level]
    step_size_l0 = int(tile_size * actual_downsample)
    
    grid_start_x = int(int(minx // step_size_l0) * step_size_l0)
    grid_start_y = int(int(miny // step_size_l0) * step_size_l0)
    grid_end_x = int(int(math.ceil(maxx / step_size_l0)) * step_size_l0)
    grid_end_y = int(int(math.ceil(maxy / step_size_l0)) * step_size_l0)
    
    tiles_info = []
    os.makedirs(output_dir, exist_ok=True)
    
    for y in range(grid_start_y, grid_end_y, step_size_l0):
        for x in range(grid_start_x, grid_end_x, step_size_l0):
            tile_box = box(x, y, x + step_size_l0, y + step_size_l0)
            intersection_area = polygon.intersection(tile_box).area
            if intersection_area > (step_size_l0 * step_size_l0 * 0.01):
                tile = slide.read_region((x, y), best_level, (tile_size, tile_size))
                tile = np.array(tile.convert('RGB'))
                
                # 1. 开关：是否保存原始切图
                if SAVE_RAW_TILES_ENABLED:
                    tile_path = os.path.join(output_dir, f"tile_{x}_{y}.png")
                    imwrite_unicode(tile_path, cv2.cvtColor(tile, cv2.COLOR_RGB2BGR))
                    path = tile_path
                else:
                    # 如果不保存，记录为空路径，后续直接从内存读取
                    path = "" 
                
                tiles_info.append({"x": x, "y": y, "level": best_level, "path": path, "downsample": actual_downsample})
    print(f"  [切图] 完成，共生成 {len(tiles_info)} 张图片数据（内存中）。")
    return tiles_info

# ----------------- 第二步：归一化 -----------------
def step2_normalize(tiles_info, normalizer, stain_matrix, output_dir, slide):
    # 2. 开关：是否执行整个归一化过程
    if not NORMALIZATION_ENABLED:
        print("[归一化] 算法已临时关闭，直接使用原始数据进行分析。")
        return tiles_info
        
    os.makedirs(output_dir, exist_ok=True)
    for tile_info in tiles_info:
        # 从内存中读取原始图像 (因为可能没有保存raw图片)
        tile = slide.read_region((tile_info["x"], tile_info["y"]), tile_info["level"], (TILE_SIZE, TILE_SIZE))
        source_bgr = np.array(tile.convert('RGB'))
        source_img = cv2.cvtColor(source_bgr, cv2.COLOR_BGR2RGB)

        # 执行归一化计算
        final_output = apply_normalization(source_img, normalizer, stain_matrix, STAIN_TYPE)
        
        # 3. 开关：是否保存归一化图片
        if SAVE_NORMALIZED_TILES_ENABLED:
            norm_path = os.path.join(output_dir, f"tile_{tile_info['x']}_{tile_info['y']}_norm.png")
            imwrite_unicode(norm_path, cv2.cvtColor(final_output, cv2.COLOR_RGB2BGR))
            tile_info["norm_path"] = norm_path
        else:
            tile_info["norm_path"] = "" # 不保存
    print(f"  [归一化] 计算完成（图片未落盘/已落盘）。")
    return tiles_info

# ----------------- 第三步：分析（针对多个ROI） -----------------
def step3_analyze(tiles_info, polygons, output_dir, tissue_output_dir, slide):
    print("[分析] 开始分析组织与阳性面积...")
    os.makedirs(output_dir, exist_ok=True)
    results = []
    
    for poly_idx, polygon in enumerate(polygons):
        polygon_clean = polygon.buffer(0)
        for tile_info in tiles_info:
            # 核心：始终从内存(WSI)动态读取图像，保证分析不依赖文件
            tile = slide.read_region((tile_info["x"], tile_info["y"]), tile_info["level"], (TILE_SIZE, TILE_SIZE))
            img_rgb = np.array(tile.convert('RGB'))
            img_bgr = cv2.cvtColor(img_rgb, cv2.COLOR_RGB2BGR)
            
            tile_x, tile_y = tile_info["x"], tile_info["y"]
            actual_downsample = tile_info["downsample"]
            img_h, img_w = img_rgb.shape[:2]
            tile_box_global = box(tile_x, tile_y, tile_x + img_w * actual_downsample, tile_y + img_h * actual_downsample)
            intersection_poly = polygon_clean.intersection(tile_box_global)
            
            if intersection_poly.is_empty:
                continue
            
            # 生成掩膜
            roi_mask = np.zeros((img_h, img_w), dtype=np.uint8)
            poly_coords_list = []
            if intersection_poly.geom_type == 'Polygon':
                poly_coords_list.append(np.array(intersection_poly.exterior.coords))
                for interior in intersection_poly.interiors:
                    poly_coords_list.append(np.array(interior.coords))
            elif intersection_poly.geom_type == 'MultiPolygon':
                for sub_poly in intersection_poly.geoms:
                    poly_coords_list.append(np.array(sub_poly.exterior.coords))
                    for interior in sub_poly.interiors:
                        poly_coords_list.append(np.array(interior.coords))
            
            local_polys = []
            for coords in poly_coords_list:
                local_coords = coords.copy()
                local_coords[:, 0] -= tile_x
                local_coords[:, 1] -= tile_y
                local_coords /= actual_downsample
                local_polys.append(local_coords.astype(np.int32))
            
            if local_polys:
                cv2.fillPoly(roi_mask, local_polys, 255)
            
            # A. 组织面积
            gray = cv2.cvtColor(img_rgb, cv2.COLOR_RGB2GRAY)
            org_mask = ((gray > ORG_DARK_THRESH) & (gray < ORG_BRIGHT_THRESH) & (roi_mask > 0)).astype(np.uint8) * 255
            if STAIN_TYPE == 'Tissue':
                kernel = np.ones((3, 3), np.uint8)
                org_mask = cv2.morphologyEx(org_mask, cv2.MORPH_OPEN, kernel)
                org_mask = cv2.morphologyEx(org_mask, cv2.MORPH_CLOSE, kernel)
            org_area_um2 = np.sum(org_mask > 0) * (TARGET_MPP ** 2)
            
            # B. 阳性面积
            if STAIN_TYPE.startswith('IHC'):  # ✅ 统一处理 IHC-positive, IHC-brown, IHC-Ki67
                pos_mask = get_ihc_positive_mask(img_bgr, STAIN_TYPE)
            elif STAIN_TYPE == 'Tissue':
                pos_mask = np.zeros_like(org_mask)
            else:
                b, g, r = cv2.split(img_bgr)  # ✅ 这里必须用正确的 img_bgr
                pos_mask = ((b > POS_B_THRESH) & (r < POS_R_THRESH) & (g < POS_G_THRESH) & (roi_mask > 0)).astype(np.uint8) * 255
            
            pos_mask = cv2.bitwise_and(pos_mask, roi_mask)
            pos_area_um2 = np.sum(pos_mask > 0) * (TARGET_MPP ** 2)
            
            # C. 伪彩
            overlay = img_rgb.copy()
            if STAIN_TYPE == 'Tissue':
                overlay[org_mask > 0] = (overlay[org_mask > 0] * 0.5 + np.array([255, 255, 0]) * 0.5).astype(np.uint8)
            else:
                overlay[pos_mask > 0] = [0, 255, 0]
            
            # 4. 开关：是否保存“阳性区域伪彩图”
            if SAVE_OVERLAY_TILES_ENABLED:
                overlay_path = os.path.join(output_dir, f"tile_{tile_info['x']}_{tile_info['y']}_overlay.png")
                imwrite_unicode(overlay_path, cv2.cvtColor(overlay, cv2.COLOR_RGB2BGR))

            # 5. 【新增】保存“全组织区域伪彩图”（柔和颜色，不刺眼）
            if SAVE_TISSUE_OVERLAY_ENABLED:
                os.makedirs(tissue_output_dir, exist_ok=True)  # 确保目录存在
                tissue_overlay = img_rgb.copy()
                tissue_color = np.array([180, 230, 240])
                tissue_overlay[org_mask > 0] = (
                    tissue_overlay[org_mask > 0] * 0.3 + tissue_color * 0.7
                ).astype(np.uint8)
                
                tissue_overlay_path = os.path.join(tissue_output_dir, f"tile_{tile_info['x']}_{tile_info['y']}_tissue_overlay.png")
                imwrite_unicode(tissue_overlay_path, cv2.cvtColor(tissue_overlay, cv2.COLOR_RGB2BGR))
            
            # D. 记录数据 (继续统计)
            results.append({
                "ROI_Index": poly_idx,
                "Tile_ID": f"tile_{tile_info['x']}_{tile_info['y']}",
                "X": tile_info["x"], "Y": tile_info["y"],
                "Org_Area_um2": org_area_um2,
                "Pos_Area_um2": pos_area_um2,
                "Pos_Ratio": pos_area_um2 / org_area_um2 if org_area_um2 > 0 else 0
            })
    
    print(f"  [分析] 完成，共处理 {len(results)} 个图块。")
    return results

# ----------------- 主流程（批量处理） -----------------
def main():
    if not os.path.exists(OUTPUT_BASE_FOLDER):
        os.makedirs(OUTPUT_BASE_FOLDER)
    
    # 1. 读取Excel
    df = pd.read_excel(EXCEL_PATH)
    print(f"成功读取 Excel，共包含 {len(df)} 个切片待处理。\n")
    
    normalizer, stain_matrix = prepare_normalizer(REF_IMG_PATH, STAIN_TYPE)
    all_summary_data = []
    
    # 2. 遍历每个切片
    for index, row in df.iterrows():
        # 读取通用的 wsi_path 和 annotation_path
        wsi_path = row['WSI_Path'] 
        annotation_path = row['Annotation_Path']
        # 强制使用annotation_path作为output文件夹名称（也可以改为wsi_path）
        slide_name = os.path.splitext(os.path.basename(annotation_path))[0]

        # 自动补全路径（相对路径变成绝对路径）
        if not os.path.exists(annotation_path):
            excel_dir = os.path.dirname(os.path.abspath(EXCEL_PATH))
            annotation_path = os.path.join(excel_dir, annotation_path)
            
        if not os.path.exists(annotation_path):
            print(f"  [错误] 找不到坐标文件: {annotation_path}，请检查 Excel 中的路径。")
            continue

        # 创建该切片的主输出文件夹（只创建一次）
        slide_output_dir = os.path.join(OUTPUT_BASE_FOLDER, slide_name)
        os.makedirs(slide_output_dir, exist_ok=True)
        
        print(f"\n======= 正在处理: {slide_name} =======")
        
        # 【核心流程】
        try:
            slide = openslide.OpenSlide(wsi_path)
            
            # 自动识别格式，解析出多边形 (只执行一次)
            ext = os.path.splitext(annotation_path)[1].lower()
            if ext == '.geojson':
                polygons, feature_names = load_polygons_from_geojson(annotation_path)
            elif ext == '.xml':
                polygons, feature_names = load_polygons_from_3dhistech_xml(annotation_path)
            else:
                raise ValueError(f"不支持的坐标文件格式: {annotation_path}")        
            
            # 【新增修复】统一清理多边形拓扑，防止 TopologyException
            cleaned_polygons = []
            for poly in polygons:
                if not poly.is_valid:
                    poly = poly.buffer(0)  # 先尝试修复
                cleaned_polygons.append(poly)
            polygons = cleaned_polygons

            print(f"  [信息] 成功读取坐标文件，共包含 {len(polygons)} 个选区。")
            
            # 第一步：切图
            all_tiles_info = []
            for poly in polygons:
                # 注意：这里一定传 slide
                tiles = step1_extract_tiles(slide, poly, os.path.join(slide_output_dir, "1_raw_tiles"))
                all_tiles_info.extend(tiles)
            
            # 第二步：归一化
            all_tiles_info = step2_normalize(all_tiles_info, normalizer, stain_matrix, os.path.join(slide_output_dir, "2_normalized_tiles"), slide)
            
            if not ANALYSIS_ENABLED:
                slide.close()
                continue
            
            # 第三步：分析（注意这里传 slide）
            results = step3_analyze(
                all_tiles_info, polygons,
                os.path.join(slide_output_dir, "3_overlay_tiles"),
                os.path.join(slide_output_dir, "4_tissue_overlay_tiles"),
                slide
            )
            
            # 第四步A：保存该切片的详细结果CSV
            detailed_csv_path = os.path.join(slide_output_dir, f"{slide_name}_analysis.csv")
            pd.DataFrame(results).to_csv(detailed_csv_path, index=False, encoding='utf-8-sig')
            
            # 第四步B：计算汇总数据
            total_org = sum([r['Org_Area_um2'] for r in results])
            total_pos = sum([r['Pos_Area_um2'] for r in results])
            percentage = (total_pos / total_org * 100) if total_org > 0 else 0
            
            all_summary_data.append({
                "NDPI_Filename": slide_name,
                "Total_Org_Area_um2": total_org,
                "Total_Pos_Area_um2": total_pos,
                "Positive_Percentage_%": percentage
            })
            
            print(f"  [结果] 切片处理完毕。总组织面积: {total_org:.2f} um², 总阳性面积: {total_pos:.2f} um², 阳性占比: {percentage:.2f}%")
            
            slide.close()
            
        except Exception as e:
            print(f"  [错误] 处理切片 {slide_name} 时发生异常: {e}")
            continue
    
    # 5. 输出总汇总表
    if ANALYSIS_ENABLED and all_summary_data:
        summary_df = pd.DataFrame(all_summary_data)
        summary_csv_path = os.path.join(OUTPUT_BASE_FOLDER, "Master_Summary_Results.csv")
        summary_df.to_csv(summary_csv_path, index=False, encoding='utf-8-sig')
        print(f"\n🎉 批量处理全部完成！总汇总表已保存至: {summary_csv_path}")
    elif not ANALYSIS_ENABLED:
        print("\n🎉 批量归一化处理完成！请在各自切片的 2_normalized_tiles 文件夹中查看结果。")

if __name__ == "__main__":
    main()