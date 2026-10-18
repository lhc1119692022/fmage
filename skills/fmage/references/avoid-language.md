# Targeted Positive And Avoid Language

Use this optional reference in the default Fmage expansion entry for additional
task-specific wording and examples. The scene trigger and minimum prompt
requirements are self-contained in `SKILL.md`; they still apply when this file
is not read. Choose relevant phrases during the existing single pass. Do not
append the whole reference, invent missing creative requirements, or rewrite an
already compliant prompt to fill a template. The direct entry does not use this
pack.

## Meaning and expression

- Default to connected natural-language prose, normally one paragraph. Desired
  appearance, exclusions, preservation requirements, and integration can all
  appear in the same paragraph. Their meanings must remain clear; labels and
  separate blocks are not required. A complex scene does not by itself call
  for a field-based template.
- Respect the user's requested format and an already-complete prompt's form.
  Use labels only when requested or needed to disambiguate complex image roles,
  exact text, or edit invariants. In that case, optional labels such as `Change`,
  `Keep unchanged`, `Match`, or `Avoid` may help; do not fill an inventory of
  fields. Outside such cases, express those ideas in ordinary sentences.
- `Asset type`, `Color palette`, and `Materials/textures` are information
  dimensions. Borrow useful details without printing their names. Asset type
  identifies the deliverable's use when supported, such as a landing-page hero
  or a character reference sheet. It does not imply an invented commercial use,
  output size, quality, crop, or aspect ratio.
- Palette choices need requested colors, relevant reference colors, or support
  from the brief. Preserve brand colors; omit unsupported color choices. Keep
  local object colors distinct from lighting color casts.
- Material/texture cues concern concrete materials, surface finish, texture
  scale, and where texture belongs, distinct from the visual medium. Do not
  turn a material cue into an instruction to texture every surface.
- Select a short positive goal and only the exclusions that address a relevant
  failure mode. Skip redundant clauses and unused fields. Use natural language
  in the prompt's language, not a separate `negative_prompt` parameter.
- Explicit content, reference roles, and the user's style take precedence.
  Do not add blanket bans on text, logos, grain, blur, gradients, cropping,
  or background elements when they belong to the requested result.
- Do not introduce Stable Diffusion-style weighted syntax, embedding names,
  repeated superlatives, or generic piles such as `masterpiece, best quality,
  ultra-detailed` / `worst quality, low quality, bad anatomy`. Preserve explicit
  user wording; apply this rule to the assistant's additions. Cleanliness cues
  do not select a higher quality tier, resolution, or a different provider.

## Task-specific phrase pairs

These are optional patterns, not universal defaults. Adapt their scope and
wording to the request; examples do not authorize new subjects or visual styles.

| Applicable task | Positive goal or preservation requirement | Targeted exclusion wording |
| --- | --- | --- |
| Exact copy and labels | Render the supplied wording once in the specified location, with legible spacing. Preserve required product labels. | Invented copy, duplicate lettering, extra characters, or unrelated glyphs. |
| Brand or product asset | Keep approved logos, product geometry, label layout, and material finish faithful to the inputs. | Invented brand marks, visible watermark-like overlays, or signature-like scribbles. |
| Character continuity | Keep the specified identity, facial proportions, hair, and outfit; preserve pose/expression only when required. | Unrequested identity redesign, duplicate facial contours, or additional accessories. |
| Local edit | Change only the named region; preserve the surrounding composition, colors, geometry, and details. | Alterations outside the requested edit, added objects, or seams at the edited boundary. |
| Multi-image composite | Match scale, perspective, light direction, contact shadows, and color temperature to the base scene. | Pasted-on halos, duplicated contours, inconsistent shadow directions, or texture bleeding between objects. |
| Natural photography | Use believable materials and lighting; retain the skin or fabric detail appropriate to the requested look. | Waxy smoothing, unwanted retouching, or artificial surface speckling. Preserve requested grain. |
| Flat mark or icon | Use readable silhouettes and deliberate flat color areas when the brief asks for a flat treatment. | Unrequested bevels, 3D mockup staging, photographic texture, or gradients. |
| Diagram or slide | Keep the required labels, arrows, data, and visual hierarchy legible. | Invented labels or data, duplicate symbols, or decorative clutter that obscures the content. |
| Transparent cutout | Preserve the subject silhouette and fine edges on actual transparency. | Painted checkerboards, unwanted opaque backdrops, edge halos, or new scenery. Preserve requested shadows. |

Watermark exclusions concern unwanted visible marks generated into the artwork,
including faint repeated lettering and signature-like patterns. They do not
authorize removing required text, approved logos, or existing reference marks
that the user asked to preserve. Refer to the unwanted mark specifically when
the user requests its removal; do not erase all typography.

## Scene clarity, including GPT Image

Scene content activates the main skill's clarity requirements. A model-family
check, an explicit "wide scene" label, and reading this reference are not gates.
For a provider nickname with no model ID in context, apply the same general
visual wording without claiming a model family. Known GPT Image models use
these requirements too; do not switch models or add a provider/config lookup.

Examples of scope:

| Request evidence | How to apply the core rule |
| --- | --- |
| One vehicle on grass before mountain ranges and open sky, with only a provider nickname | Trigger from environment and depth; control sky continuity, grass detail distribution, vehicle edges, and focal contrast. |
| A crowded street, layered vegetation, a large interior, or overlapping reflective materials | Trigger from scene complexity; preserve the requested elements while making their grouping, texture scale, and boundaries readable. |
| An image composite or local artifact-cleanup edit | Apply to the requested integration or repair; lock unaffected regions, lighting, palette, and legitimate texture. |
| A textured watercolor landscape, grainy film scene, or balanced multi-focus composition | Preserve that medium and layout; constrain only unwanted artifacts, not the requested grain or focal arrangement. |
| A simple icon or isolated product on a plain background, described as 16:9, 4K, or max quality | These delivery settings do not trigger scene treatment; use only relevant ordinary asset guidance. Explicit cleanup still qualifies. |

Treat dirty textures, grid-like noise, ghosted outlines, and excessive detail as
visible outcomes to control. These symptoms alone do not establish training
overfitting or any other internal model cause. This is prompting guidance, not
a guarantee that artifacts will disappear.

For a new wide or complex scene, establish readable forms and a focal hierarchy
before surface detail. The submitted prompt needs both positive control and
corresponding targeted exclusions. These rows offer wording for the applicable
requirements; weave the relevant meanings into natural prose. Equivalent
phrasing is sufficient, with no required labels, clause order, or stock ending:

| Visual goal | Positive wording | Corresponding targeted exclusion |
| --- | --- | --- |
| Large clean shapes | Organize the scene into broad, readable masses with coherent silhouettes and separated depth layers. | Dirty grid-like noise or broken repetitive patterns across otherwise continuous surfaces. |
| Controlled edges | Keep the focal silhouette and important boundaries clear; let distant edges soften naturally with depth. | Ghosted/doubled contours, pasted-on halos, or brittle sharpening fringes. |
| Dominant focus | Give the requested focal subject the clearest local contrast and edge definition; let supporting elements remain readable and quieter. | Accidental competing high-contrast details that obscure the intended focus. |
| Limited texture | Concentrate material detail where it explains the subject; use quieter continuous surfaces and coarser detail at a distance. | All-over microtexture, repetitive etched detail, texture bleeding, or random mottled speckling. |
| Managed contrast | Group light and shadow into coherent areas, with smooth tonal transitions and selective local contrast. | Muddy patchwork shading, harsh microcontrast, or random bright/dark speckles. |

Use plain descriptions such as "repetitive etched detail" or "all-over
microtexture" for what the user calls overfitted texture. Do not send an
unverifiable algorithmic diagnosis as the instruction. Add a specific exclusion
for invented visible watermarks, repeated pseudo-lettering, or signatures when
relevant; do not claim that every noise pattern is a watermark.

Do not infer film grain, floating dust/pollen, or uniformly sharp grass and rock
from "cinematic", "photorealistic", or a high quality/resolution setting. Replace
assistant-added superlatives with concrete material, distance, edge, and contrast
instructions. A single "no watermark" or "no HDR halos" is insufficient for a
scene that needs control over texture distribution and continuous surfaces.

Compatibility rules:

- Clean shapes do not mean an empty scene, flat vector art, or deleted objects.
  Keep the requested crowd, architecture, foliage, scale, and narrative details.
- A dominant focus is a hierarchy, not a one-object limit. Preserve explicitly
  balanced multi-focus compositions, diagrams, or panels; establish clarity
  within their existing layout rather than imposing one focal subject.
- Controlled texture is not plastic smoothing. Preserve material identity,
  intended film grain, watercolor paper, brushwork, weathering, and fine subject
  detail. Limit only unwanted overlays or excessive detail outside those areas.
- Distinguish noise from real grids, mesh, textiles, windows, and repeating
  architecture; ghosting from intended motion blur, reflections, or atmosphere;
  dirty speckling from requested dirt, rain, snow, or patina.
- Managed contrast is not universally low contrast or desaturation. Preserve
  requested dramatic light, deep blacks, vivid colors, and chiaroscuro.
- For edits, keep composition, focus, palette, and legitimate textures fixed
  unless the requested change includes them. Local cleanup does not authorize
  global sharpening, smoothing, recoloring, reframing, or redesign.

## Examples of natural-language prompts

These examples illustrate semantic coverage, not a reusable sentence order.
Adapt wording and length to the request. The exclusions are part of the prose;
an `Avoid:` heading is unnecessary. If the user requests a labelled format,
organize the same meanings under only the relevant labels.

### New wide scene

Example brief: a wide harbor illustration at dawn with a ferry as the focal
subject and a slate-blue/coral palette, for a story opening. Do not import this
scene or palette into unrelated requests.

```text
Create a wide harbor illustration at dawn for a story opening, using the requested slate-blue and coral palette. A ferry is the focal subject, its clear silhouette standing against broad waterfront masses and distinct depth layers, with quieter supporting buildings. Dawn light forms coherent areas of light and shadow, with smooth tonal transitions and selective contrast around the ferry. Keep boat and masonry texture restrained near the focal area, simplify distant detail, and leave the water and sky continuous, without grid-like noise, doubled outlines, repetitive etched microtexture, random speckling, or invented visible watermark-like lettering.
```

### Provider nickname, no model identification

Example brief: use a named provider to photograph a dark-green electric SUV on a
grassy hill before mountain ranges under a clear sky, with cinematic lighting.
The environment and depth trigger scene clarity before the provider's model ID
is known. Keep requested aspect, resolution, and quality in tool parameters;
they do not imply grain or additional microdetail.

```text
Photograph a dark-green electric SUV parked on a grassy hill beneath a clear blue sky, with mountain ranges receding in distinct layers behind it. Cinematic natural sunlight gives the bodywork coherent highlights and shadows, with selective contrast and clear contours keeping the vehicle dominant. Render the paint, glass, and tires believably; let nearby grass retain restrained detail that softens with distance into quieter mountain surfaces. The hill and mountain masses should stay readable, with naturally softer distant ridges and smooth sky and shadow transitions. Keep the sky free of grid-like noise and mottled speckling, and avoid repetitive etched grass texture, doubled vehicle contours, sharpening fringes, and invented visible watermark-like lettering.
```

This example's subject and palette belong only to this brief. It does not
authorize changing other requests into automotive photography. If the user asks
for a grainy film rendition, preserve that grain and constrain unrelated grid
artifacts and doubled contours instead.

### Local cleanup without style loss

Example brief: remove grid-like artifacts and doubled outlines from the distant
buildings of an existing harbor illustration; retain its watercolor grain.

```text
In Image 1, the edit target, remove only the unwanted grid-like artifacts and doubled outlines on the distant buildings. Restore coherent facade surfaces and natural distance-softened edges while retaining the original watercolor paper grain and genuine window patterns. Match the repaired areas to the illustration's perspective, surface color, and edge softness. Keep the buildings' geometry, ferry, water, sky, framing, focal hierarchy, palette, lighting, brushwork, and every other region unchanged. Do not introduce pseudo-lettering, visible watermark patterns, repeated ghost contours, global smoothing, or additional microtexture.
```
