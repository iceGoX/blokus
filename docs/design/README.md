# 角域 · 奶油拼趣

[可编辑 Pixso 设计文件](https://pixso.net/app/design/EkiwN1Njlbxn043HnBJpVA)，位于 Pixso 草稿的 icego.tech 文件夹内。文件包含大厅与候场、三端对局、弹窗与状态三组设计板；文本、容器、格线和棋子为可编辑图层，插画为嵌入图片。

网站复用原有页面状态与事件绑定，按已确认设计调整 HTML、CSS 和视觉资源，不使用 Pixso 导出的静态示例替换游戏脚本。

- [手机大厅](mobile-entry.png)
- [桌面对局](desktop-game.png)
- [大厅设计对照](entry-comparison.png)
- [对局设计对照](game-comparison.png)
- [验证记录](../../design-qa.md)

assets/design 下的 JPEG 为本次用内置 imagegen 生成并压缩的视觉资源：大厅插画。装饰插画中的棋局不作规则示例；实际棋盘由原有代码绘制。

默认字体使用系统中文字体回退；Pixso 中的思源宋体在不同系统上可能有轻微字形差异。未将 Pixso 临时导出的字体文件或静态示例代码加入发布产物。

为保留现有玩家身份与文案，第四方沿用绿方并采用柔和绿色；默认主题保留奶油、薄荷、杏黄、珊瑚主色。外观入口仍可切换现有另外两种主题。
