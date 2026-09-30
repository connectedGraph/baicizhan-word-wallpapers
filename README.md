# 百词斩 · 早起单词壁纸 (baicizhan-word-wallpapers)

百词斩 App「早起打卡」活动历史上发布过的**单词海报图片数据集**。

> 现状声明：百词斩「早起打卡」海报接口为 App 内接口，现已失效。本仓库不再提供抓取脚本，仅存档历史收集到的 451 张海报图片，并附一个可在线观赏的静态 gallery 页面。

## 📸 在线观赏

点击 → **[早起单词壁纸 Gallery](https://connectedGraph.github.io/baicizhan-word-wallpapers/)** （GitHub Pages 部署）

- **随机一张**：一键随机弹出海报
- **全屏观赏**：点击任意图片放大，左右箭头 / 键盘 ←  → 切换
- **搜索**：按单词或日期筛选

页面完全静态、单 HTML，无后端。若不想用 Pages，直接双击本地 `index.html` 同样可离线浏览（需与 `海报数据/` 保持同目录）。

## 📁 目录结构

```
baicizhan-word-wallpapers/
├── index.html                # gallery 观赏页(随机 + 翻看 + 搜索)
├── LICENSE                   # CC BY-NC 4.0
├── 海报数据/                 # ★ 全部 451 张历史海报(大文件走 Git LFS)
│   ├── manifest.json         # 图片文件名清单(gallery 用)
│   └── poster_*.png
└── .gitattributes            # LFS 规则
```

## 🖼 数据集

`海报数据/` 内含 **451 张** PNG 海报，命名形如：

```
poster_3_20_16_20160407171258_44856.png
```

文件名含义：`poster_<3>_<20>_<16>_<时间戳>_<随机数>.png`，主要区分点是时间戳（201603 ～ 201704）。图片版权归百词斩所有，仅供个人学习与存档。

### 本地开发 / 自建 Gallery

gallery 通过 `海报数据/manifest.json` 发现图片。新增图片后重新生成该清单：

```bash
ls 海报数据/*.png | sed 's#海报数据/##' | python -c \
  "import sys,json; print(json.dumps({'files':[l.strip() for l in sys.stdin]}))" \
  > 海报数据/manifest.json
```

### GitHub Pages 部署

仓库 Settings → Pages → Source 选 `Deploy from a branch` → 分支 `main`、目录 `/ (root)` → Save。约 1 分钟后访问 `https://<你的用户名>.github.io/baicizhan-word-wallpapers/`。

## ⚠️ 注意

- **图片走 Git LFS**：clone 仓库时 LFS 图片默认按需拉取。若用 Pages 直接托管，GitHub 会在页面上提供真实的 LFS 文件（Pages 支持 LFS 对象渲染）。
- 如需自建 Gallery 展示，需保证图片能被 Pages/服务器正常 serve（LFS 图片在 Pages 上会还原为原始文件）。

## 📄 LICENSE

[CC BY-NC 4.0](https://creativecommons.org/licenses/by-nc/4.0/)（署名-非商业性使用）。

- **可**：署名引用、复制、非商业性修改与分发
- **不可**：商业性使用
- 图片素材版权归百词斩及原作者所有，仅用于学习与存档

> 若百词斩或相关权利人认为不妥，请联系移除。