# normal people — design system

a quiet, unbranded brand. type does the work.
no decoration. no marketing energy. no slick gradients.

---

## ethos

- everything in lowercase
- one accent, no more
- one type family
- empty space is a feature, not a gap
- if it doesn't serve clarity, it doesn't ship

---

## palette — matte, low chroma

### neutrals (the field)

| role        | hex       | usage                              |
|-------------|-----------|------------------------------------|
| paper       | `#EDE9E0` | primary background — warm off-white, matte |
| paper alt   | `#E5E1D8` | surfaces a half-step deeper        |
| ink         | `#1F1D1A` | primary text — near-black, warm    |
| ink soft    | `#4A4843` | secondary text                     |
| ink faint   | `#9A968D` | tertiary, placeholders, captions   |
| rule        | `#C8C3B8` | dividers, borders                  |

### accents — two, paired

| role        | hex       | usage                              |
|-------------|-----------|------------------------------------|
| signal      | `#7A3B2E` | primary accent. links, the 'n' shape, the moment your eye lands. dried-blood / oxidized copper / muted brick. |
| signal hover| `#5C2C22` | darker for interaction states      |
| ochre       | `#B68A2E` | secondary accent. the 'p' shape, secondary callouts, dataviz second series. muted mustard / dried turmeric. |
| ochre hover | `#8E6B22` | darker for interaction states      |

these two colors never appear together except in the monogram itself, or paired across distinct elements. never gradient between them. never use them on the same continuous text run.

### dark mode (mirror)

| role        | hex       | usage                              |
|-------------|-----------|------------------------------------|
| paper       | `#1A1815` | primary background                 |
| paper alt   | `#23211D` | surfaces                           |
| ink         | `#E8E4DA` | primary text                       |
| ink soft    | `#A8A49B` | secondary                          |
| ink faint   | `#6E6A62` | tertiary                           |
| rule        | `#3A3733` | dividers                           |
| signal      | `#C76551` | primary accent on dark fields      |
| ochre       | `#D4A547` | secondary accent on dark fields    |

### contrast notes

- ink on paper: 14.6:1 — passes AAA
- ink soft on paper: 7.8:1 — passes AAA
- signal on paper: 5.4:1 — passes AA for body text
- never use ink faint on paper alt for anything important

---

## typography

### type family

**inter** — the only typeface used across the entire system. variable weights 400, 500, 700.
not Helvetica. not system fonts. inter is plain, modern, and reads at 10px.

### weights

- regular (400) — body, almost everything
- medium (500) — emphasis, the wordmark
- bold (700) — used sparingly, large text only

### sizes (web reference)

| role          | size        | weight | tracking |
|---------------|-------------|--------|----------|
| display       | 48px / 56px | 500    | -0.02em  |
| h1            | 32px / 40px | 500    | -0.015em |
| h2            | 24px / 32px | 500    | -0.01em  |
| body          | 16px / 26px | 400    | 0        |
| small         | 14px / 22px | 400    | 0        |
| caption       | 12px / 18px | 400    | +0.01em  |

### rules

- never set body text in bold
- never use italics (the brand doesn't lean)
- never letterspace lowercase
- everything left-aligned, ragged right
- one-thought-per-line for poetic copy (manifesto, agreements)
- prose paragraphs for documentation

---

## logo system

the brand has two marks. they are not interchangeable.

### the wordmark

```
normal people
```

set in inter medium (500), tracking -0.015em.
all lowercase, no punctuation, no period.
single space between words.
no stylization, no ligature, no custom letterform.
the wordmark is the brand at speech volume.

### the monogram — `np`

the primary visual identity for surfaces where the full wordmark is too large or unreadable.
construction: a filled circle (signal red) holding a lowercase 'n', overlapping a slightly-rounded square (ochre) holding a lowercase 'p'. both glyphs in inter medium, paper-colored, sized assertively so they dominate their shapes.

the two shapes are deliberately separated by color and form but unified by overlap. one is round (n / signal), the other is squared (p / ochre). together they read as a singular mark.

ratios (canonical, 512×512 square):
- circle radius: 110
- square: 220×220, corner radius 12, overlapping the circle's right edge
- glyphs: 200pt inter medium, letter-spacing -0.04em, paper-fill

use the monogram for:
- avatars (bot, supergroup, library, any social profile)
- favicons
- the corner stamp on documents and headers
- any context where the wordmark would be unreadable

files:
- `brand/assets/np-monogram-square.svg` / `.png` — for avatars (512×512)
- `brand/assets/np-monogram-lockup.svg` / `.png` — for headers (1024×512)

### lockup variants

| variant                       | use                                    |
|-------------------------------|----------------------------------------|
| monogram + wordmark           | hero headers, primary marketing        |
| monogram only (square)        | avatars, favicons, watermarks          |
| monogram only (horizontal)    | wide hero banners, document headers    |
| wordmark only                 | footers, tight contexts, in-line use   |

### clear space

minimum padding around any lockup = the radius of the monogram's circle.
no exceptions.

---

## photography & imagery

**there is none.** no stock photos. no illustrations. no decorative graphics.
if an image must exist, it is functional: a chart, a reagent color reference, a diagram.
the absence of imagery is the strongest visual statement we can make.

---

## surfaces — telegram

### bot avatar (`@normalpeople_gateway_bot`)
- 512×512 png
- np monogram, centered, paper background
- file: `assets/np-monogram-square.png`

### library channel avatar
- 512×512 png
- np monogram, centered, paper background
- file: `assets/np-monogram-square.png` (identical to bot)

### floor supergroup avatar
- 512×512 png
- np monogram, centered, paper background
- file: `assets/np-monogram-square.png` (identical to bot)

*the three surfaces share one avatar to reinforce that bot, library, and floor are one entity.*

### bot dm chat background
- subtle, low-contrast
- paper field with a quiet repeating element: faint dot grid at 8% opacity
- 1080×1920 (mobile), 2560×1440 (desktop)
- file: `assets/bot-background.png`

---

## voice & tone (linked to copy)

- declarative beats, not flowing prose
- one thought per line for principles
- never use exclamation points
- never use marketing verbs ("unlock", "elevate", "discover")
- never call ourselves a community in marketing voice — we *are* one
- if it sounds like a startup, rewrite it
