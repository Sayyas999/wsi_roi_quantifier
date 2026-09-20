# 病理切片自动化定量分析系统 (WSI ROI Quantifier)

本项目提供了一套完整的**全切片数字病理图像（WSI）**自动化分析流程，支持从感兴趣区域（ROI/AOI）的自动切割，到可选的颜色归一化，再到免疫组化（IHC）或特殊染色的阳性区域定量统计。

项目包含两个核心脚本：

- **wsi_roi_quantifier.py**：批量处理主管线（切图、归一化、分析、导出）。
- **hsv_tuner.py**：HSV 颜色参数实时调试工具。

---

## ✨ 核心功能特性

- **基于 QuPath / 3DHISTECH 的 ROI 精准定量**  
  自动读取 QuPath 导出的 GeoJSON 或 3DHISTECH SlideMaster 导出的 XML 标注文件，结合 ROI 掩膜机制，确保只统计选区内面积，杜绝选区外组织污染。

- **绝对无重叠的切图逻辑**  
  根据扫描倍率自适应调整网格步长，实现 100% 无缝拼接，从根本上解决同一组织重复统计的问题。

- **多染色策略自适应**  
  - **HE / Masson / PAS**：采用 Macenko 算法自动提取主成分并结合反卷积，实现优秀的颜色归一化（解决切片厚度不均、染色批次差异导致的颜色明暗深浅问题）。  
  - **Movat（五色）**：强制采用手动 OD 向量分离，确保五色分离准确。  
  - **IHC（免疫组化）**：提供 `IHC-positive`（浅黄+深褐）、`IHC-brown`（仅强阳/深褐）、`IHC-Ki67` 等模式。  
  - **Tissue（通用）**：仅做组织/背景分割，不做颜色改变。

- **基于 HSV 的 IHC 识别**  
  利用 HSV（色调、饱和度、明度）空间精确识别深浅不一的 DAB 阳性区域，完美规避蓝色细胞核干扰。可先用 `hsv_tuner.py` 调试最佳阈值。

- **自动倍率推算**  
  只需输入 `TARGET_MAGNIFICATION`（如 20），脚本自动换算目标 MPP（微米/像素），保证面积计算的物理准确性。

- **Excel 批量处理**  
  通过 Excel 表格导入 WSI 和标注文件路径，支持大批量切片一键跑完。

- **独立开关控制输出**  
  可分别控制是否保存原始切图、是否执行归一化、是否保存归一化图片、是否保存阳性伪彩图、是否保存全组织伪彩图，灵活节省硬盘空间。

---

## 📦 环境安装

建议使用 Anaconda 或 Miniconda 创建独立环境。

```bash
conda create -n wsi_analysis python=3.9
conda activate wsi_analysis

pip install opencv-python numpy pandas openpyxl
pip install openslide-python shapely geopandas scikit-image
pip install staintools
```

> **注意**：`staintools` 在 Windows 下安装可能报错（缺少 `spams`）。可手动注释掉 `staintools` 源码中 Vahadane 提取器的导入，完全不影响 Macenko 核心功能。

---

## 🛠️ 第一步：HSV 参数调试 (hsv_tuner.py)

在进行正式的 IHC / Masson 切片分析前，强烈建议先使用此工具确定最佳的 HSV 阈值。

### 1. 运行

修改 `hsv_tuner.py` 中的 `IMAGE_PATH`，填入一张有代表性的 20 倍切图（可先用主程序导出几张原图），然后运行：

```bash
python hsv_tuner.py
```

### 2. 使用方法

- **拖动滑块**：使用鼠标滑动 H Min/Max、S Min/Max、V Min/Max。  
- **实时预览**：图像窗口会实时显示绿色半透明的伪彩标记，表示选中的区域。  
- **快捷键**：  
  - 按 `p`：在控制台打印当前的 HSV 参数。  
  - 按 `s`：保存当前的叠加效果图到本地。  
  - 按 `q` 或 `Esc`：退出程序。

### 3. 调参参考

- 寻找红色：H: 0~10 或 170~179，S > 80  
- 寻找黄色/褐色：H: 20~35，S > 50  
- 寻找绿色：H: 40~85，S > 60  
- 寻找蓝色：H: 90~130，S > 80  

调好参数后，将打印出来的数值填入 `wsi_roi_quantifier.py` 中的 `get_ihc_positive_mask` 函数。

---

## 🚀 第二步：批量分析 (wsi_roi_quantifier.py)

### 1. 准备输入文件

创建一个 Excel 表格（如 `input_slides.xlsx`），包含以下两列（表头必须完全一致）：

| WSI_Path | Annotation_Path |
| :--- | :--- |
| `D:\WSI_Scans\Case1.ndpi` | `D:\WSI_Scans\ROI\Case1.geojson` |
| `D:\WSI_Scans\Case2.mrxs` | `D:\WSI_Scans\ROI\Case2.xml` |

> `Annotation_Path` 支持 `.geojson`（QuPath）或 `.xml`（3DHISTECH SlideMaster）。  
> 路径可写相对路径（与 Excel 同目录），脚本会自动补全。

### 2. 关键参数配置

打开 `wsi_roi_quantifier.py`，在配置区修改以下参数：

```python
# 输入与输出
EXCEL_PATH = r"D:\WSI_Scans\input_slides.xlsx"
OUTPUT_BASE_FOLDER = r"D:\WSI_Scans\Output_All"

# 核心分析设置
STAIN_TYPE = 'IHC-positive'   # 可选: 'HE', 'Masson', 'Movat', 'Tissue', 'IHC-positive', 'IHC-brown', 'IHC-Ki67'
TARGET_MAGNIFICATION = 20     # 界定切片分析的放大倍率（脚本自动推导 MPP）
TILE_SIZE = 2048              # 切出的图片长宽（像素）

# 独立开关（按需开启/关闭）
SAVE_RAW_TILES_ENABLED = False        # 是否保存原始切图
NORMALIZATION_ENABLED = False         # 是否执行归一化算法（仅对 HE/Masson/Movat 有效）
SAVE_NORMALIZED_TILES_ENABLED = False # 是否保存归一化后的图片
SAVE_OVERLAY_TILES_ENABLED = True     # 是否保存阳性区域伪彩图
SAVE_TISSUE_OVERLAY_ENABLED = False   # 是否保存全组织区域伪彩图

# 其他阈值（根据实际情况微调）
ORG_BRIGHT_THRESH = 215
ORG_DARK_THRESH = 20
```

> **⚠️ 关于颜色归一化**  
> 归一化仅适用于 **HE、Masson、PAS、Movat** 等特殊染色，用于解决切片厚度不均、染色批次不同导致的颜色明暗深浅差异。  
> **IHC 图像分析中不适用归一化**，请将 `NORMALIZATION_ENABLED` 设为 `False`。

### 3. 运行主程序

```bash
python wsi_roi_quantifier.py
```

---

## 📂 输出结构说明

程序运行结束后，会在 `OUTPUT_BASE_FOLDER` 下生成如下结构：

```text
Output_All/
├── Case1/                          <-- 自动创建的切片文件夹（以 Annotation 文件名命名）
│   ├── 1_raw_tiles/                <-- 切出的原图（20倍）
│   ├── 2_normalized_tiles/         <-- 归一化后的图片（如果开启）
│   ├── 3_overlay_tiles/            <-- 阳性区域伪彩叠加图
│   ├── 4_tissue_overlay_tiles/     <-- 全组织区域伪彩叠加图（如果开启）
│   └── Case1_analysis.csv          <-- 该切片单个 Tile 的详细数据表（X/Y 坐标、面积、阳性比）
│
├── Case2/
│   └── ... (同上)
│
└── Master_Summary_Results.csv      <-- 总汇总表：所有 WSI 的总组织面积、总阳性面积、阳性百分比
```

---

## 📄 License

本项目基于 **MIT License** 开源，欢迎用于学术研究和商业用途。如果使用本工具发表了论文，请引用本仓库，万分感谢！
