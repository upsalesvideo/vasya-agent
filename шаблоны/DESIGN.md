---
name: "[Название проекта]"
description: "[Одна строка: настроение и стратегия цвета, например «Committed, светлая, тёплая тонированная нейтраль + один глубокий акцент»]"
colors:
  bg: "#F7F4EF"
  fg: "#1B1A17"
  muted: "#6B675E"
  surface: "#EFEAE2"
  border: "#DDD6CA"
  accent: "#8A3B12"
  accent-hover: "#6E2E0D"
  on-accent: "#FBF7F2"
typography:
  display:
    fontFamily: "Manrope, 'Segoe UI', Arial, sans-serif"
    fontSize: "clamp(2.25rem, 6vw, 4rem)"
    fontWeight: 700
    lineHeight: 1.05
    letterSpacing: "-0.02em"
  heading:
    fontFamily: "Manrope, 'Segoe UI', Arial, sans-serif"
    fontSize: "clamp(1.5rem, 3vw, 2.25rem)"
    fontWeight: 600
    lineHeight: 1.15
  body:
    fontFamily: "Inter, 'Segoe UI', Arial, sans-serif"
    fontSize: "1.0625rem"
    fontWeight: 400
    lineHeight: 1.55
  small:
    fontFamily: "Inter, 'Segoe UI', Arial, sans-serif"
    fontSize: "0.875rem"
    fontWeight: 400
    lineHeight: 1.45
rounded:
  sm: "6px"
  md: "12px"
  pill: "999px"
spacing:
  xs: "8px"
  sm: "16px"
  md: "24px"
  lg: "48px"
  xl: "96px"
components:
  button-primary:
    backgroundColor: "{colors.accent}"
    textColor: "{colors.on-accent}"
    typography: "{typography.body}"
    rounded: "{rounded.md}"
    padding: "16px 28px"
    height: "52px"
  button-primary-hover:
    backgroundColor: "{colors.accent-hover}"
  input:
    backgroundColor: "{colors.bg}"
    textColor: "{colors.fg}"
    rounded: "{rounded.sm}"
    padding: "14px 16px"
    height: "52px"
---

## Overview

[Сцена одной фразой: кто, где, при каком свете, в каком настроении смотрит сайт → поэтому
тема светлая/тёмная.] Стратегия цвета: [Restrained / Committed / Full palette / Drenched].
Референсы владельца: [url → приём]. Регистр: brand.

## Colors

Канон — OKLCH (hex во frontmatter только для совместимости). Нейтрали подкрашены в
сторону акцента, чистых #000 / #fff нет.

| Роль | OKLCH | Где |
|---|---|---|
| bg | oklch(96% 0.01 80) | фон страницы |
| fg | oklch(20% 0.01 80) | основной текст, контраст к bg ≥ 12:1 |
| muted | oklch(50% 0.01 80) | подписи, второстепенный текст (≥ 4.5:1) |
| surface | oklch(93% 0.012 80) | выделенные секции |
| border | oklch(86% 0.015 80) | линии, рамки полей |
| accent | oklch(42% 0.14 45) | кнопка целевого действия, ссылки; ≥ 3:1 к bg |
| accent-hover | oklch(36% 0.14 45) | hover/active кнопки |
| on-accent | oklch(97% 0.01 80) | текст на акценте |

Акцент занимает [30–60 % при Committed / ≤ 10 % при Restrained] поверхности: [где именно].

## Typography

Пара: [Заголовки] + [Текст], обе с кириллицей на Google Fonts. Шкала с шагом 1.25:
display → heading → body → small. Длина строки текста 65–75 символов (`max-width: 68ch`).
Заголовки с отрицательным трекингом, текст без.

Файлы шрифтов лежат в проекте (`python fonts.py <slug> "Manrope:wght@600;700" "Inter:wght@400;500"`),
подключение: `<link rel="stylesheet" href="assets/fonts/fonts.css">` + `preload` двух главных
woff2. Ссылок на fonts.googleapis.com / fonts.gstatic.com на сайте нет.

## Elevation

Тени почти не используются: разделение блоков цветом фона (`surface`) и линиями
(`border`). Единственная тень — у плавающей кнопки на мобильном:
`0 8px 24px oklch(20% 0.01 80 / 0.18)`.

## Components

- **button-primary**: высота 52 px, скругление md, текст 500, один текст-глагол на весь
  сайт. Hover: фон accent-hover, `transform: translateY(-1px)`, 150 мс ease-out-quart.
  Active: `translateY(0)`. Фокус: `outline: 2px solid accent; outline-offset: 3px`.
- **button-secondary**: прозрачный фон, рамка border, текст fg. Только для второго
  «мягкого» шага, если он есть.
- **input**: высота 52 px, рамка border, фокус — рамка accent. Label над полем, ошибка
  под полем текстом muted → accent.
- **section**: паддинг lg на мобильном, xl на десктопе; чередование bg / surface.
- **nav**: прозрачная над hero, липкая после первого экрана, высота 64 px, на мобильном
  бургер → полноэкранное меню.

## Do's and Don'ts

Do:
- Один акцентный цвет — только на целевом действии и ссылках.
- Приёмы из референсов владельца: [перечислить].
- Движение — по этапу E `библиотеки.md` (Эмиль): transform/opacity, ≤ 400 мс, reduced-motion.

Don't:
- Glassmorphism, gradient text, side-stripe borders, hero-metric, одинаковые карточки с иконками.
- Чистый белый и чёрный, серые нейтрали без оттенка.
- [анти-примеры владельца из PRODUCT.md].
