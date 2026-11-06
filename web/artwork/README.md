# Timelapse app icon

Master artwork: `timelapse-icon.png` (1254 × 1254, RGBA).

Created with the built-in image-generation tool. Browser and home-screen assets
are in `web/public/`. PNG sizes use Lanczos downsampling; the ICO packages 16, 32,
48, and 256px PNG images. Apple and Android maskable variants have an opaque
charcoal background.

Run `npm run icons` from `web/` to rebuild the derived assets. This uses the
locally installed FFmpeg; frontend/Docker builds use the committed assets directly.

Final generation prompt:

```text
Use case: logo-brand.
Asset type: final square application icon and favicon for a camera timelapse viewer.
Primary request: design one polished, colorful, distinctive timelapse app icon based on the fast-forward symbol. Make it an actual app icon with a deliberate composition, not a monochrome line icon or a font glyph.
Subject: two large, bold, solid right-facing triangular play shapes forming fast-forward. Use a vivid teal-to-mint treatment on the first shape and warm amber-to-coral on the second. Their strong silhouette must read immediately at 16 and 32 pixels. A subtle broad orbital sweep or daylight arc integrated into the background can suggest time passing.
Style: refined modern app artwork, clean geometric shapes, restrained layered depth and softly lit edges, richly colored but uncluttered. Front-on view, square composition.
Background: a deep charcoal/navy rounded-square tile with a subtle tonal gradient. The tile fills the square canvas; only the rounded outer corners are transparent. Keep the central fast-forward shapes large, occupying roughly 70 percent of the icon width, with balanced optical spacing.
Constraints: just one finished icon, no text, no letters, no captions, no contact sheet, no device mockup, no large outer margin, no extra tiny symbols, no vertical skip bar, no watermark. Preserve a clear small-size silhouette; detail and lighting should support the large shapes.
```
