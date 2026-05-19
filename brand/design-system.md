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

### accent — one only

| role        | hex       | usage                              |
|-------------|-----------|------------------------------------|
| signal      | `#7A3B2E` | the dot. the link. the one thing your eye lands on. dried-blood / oxidized copper / muted brick. |
| signal hover| `#5C2C22` | darker for interaction states      |

### dark mode (mirror)

| role        | hex       | usage                              |
|-------------|-----------|------------------------------------|
| paper       | `#1A1815` | primary background                 |
| paper alt   | `#23211D` | surfaces                           |
| ink         | `#E8E4DA` | primary text                       |
| ink soft    | `#A8A49B` | secondary                          |
| ink faint   | `#6E6A62` | tertiary                           |
| rule        | `#3A3733` | dividers                           |
| signal      | `#C76551` | brighter accent for dark fields    |

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

### wordmark

```
normal people
```

set in inter medium (500), tracking -0.015em.
all lowercase, no punctuation, no period.
single space between words.
no stylization, no ligature, no custom letterform.
the wordmark IS the brand. the absence of styling IS the style.

### geometric mark — the dot

a single filled circle in `signal` (`#7A3B2E`).
ratio: the dot's diameter equals the x-height of the wordmark.
position when lockup is used: leading the wordmark with one space of breathing room.
position when standalone: centered on its container.

```
●  normal people
```

the dot is the brand's only ornament. it stands for:
- the period that should never appear in the wordmark
- the unblinking acknowledgment of presence
- one of us

### lockup variants

| variant           | use                                    |
|-------------------|----------------------------------------|
| dot + wordmark    | headers, hero placements               |
| wordmark only     | tight contexts, footers                |
| dot only          | avatar, favicon, watermark             |

### clear space

minimum padding around any lockup = the height of the dot.
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
- background: paper (`#EDE9E0`)
- foreground: dot only, centered, ~30% of canvas
- file: `assets/bot-avatar.png`

### library channel avatar
- same template
- variant: dot + small "library" wordmark below it
- file: `assets/library-avatar.png`

### floor supergroup avatar
- same template
- variant: dot only (matches bot)
- file: `assets/floor-avatar.png`

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
