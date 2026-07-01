-- Mercury — Aseprite import-and-tag script (step 1 of the optional
-- palette-unification post-process).
--
-- Loose PNG frames carry no tags, so the one-shot
--   aseprite -b frames/*.png --sheet ... --split-tags
-- does NOT work (--split-tags becomes a no-op and packed export is
-- broken). The correct path is two steps; this script is step 1:
-- import the ordered PNG frames into ONE sprite (one cel per frame),
-- assign animation tags, optionally apply a shared palette + convert to
-- INDEXED colour mode, then save a tagged `.ase`. Step 2 exports the
-- packed sheet + json-hash data from that `.ase`.
--
-- Invoke (the `-b` is supplied by adapters/aseprite/invoke.py):
--   aseprite -b --script import_and_tag.lua \
--     --script-param frames=<comma-separated absolute PNG paths> \
--     --script-param tags=<name:from-to;name:from-to;...> (1-based, inclusive) \
--     --script-param palette=<palette file | ""> \
--     --script-param out=<tagged.ase>
--
-- NOTE: not runtime-tested in this repo (Aseprite is intentionally
-- absent); written to the verified Aseprite v1.2.10+ Lua API.

local framesParam  = app.params["frames"]
local tagsParam    = app.params["tags"]
local paletteParam = app.params["palette"]
local outParam     = app.params["out"]

if not framesParam or framesParam == "" then
  error("import_and_tag: missing --script-param frames=...")
end
if not outParam or outParam == "" then
  error("import_and_tag: missing --script-param out=...")
end

-- Split the comma-separated frame list, preserving order.
local files = {}
for f in string.gmatch(framesParam, "([^,]+)") do
  files[#files + 1] = f
end
if #files == 0 then
  error("import_and_tag: frames list is empty")
end

-- Determine sprite size from the first frame, then create one RGB sprite
-- and lay each frame onto its own animation frame (frame 1 reuses the
-- new sprite's existing empty cel; the rest get a fresh frame + cel).
local firstImage = Image{ fromFile = files[1] }
if not firstImage or firstImage.width == 0 or firstImage.height == 0 then
  error("import_and_tag: could not load first frame: " .. files[1])
end
local sprite = Sprite(firstImage.width, firstImage.height, ColorMode.RGB)
local layer = sprite.layers[1]

sprite.cels[1].image = firstImage
sprite.cels[1].position = Point(0, 0)

-- Every frame must load and match the first frame's dimensions; a ragged
-- set would otherwise pack into a misaligned sheet. error() aborts the
-- batch run (non-zero exit), which the caller treats as a graceful skip
-- back to the pure-Python packer.
for i = 2, #files do
  local img = Image{ fromFile = files[i] }
  if not img or img.width == 0 or img.height == 0 then
    error("import_and_tag: could not load frame: " .. files[i])
  end
  if img.width ~= firstImage.width or img.height ~= firstImage.height then
    error("import_and_tag: frame " .. files[i] .. " is " .. img.width .. "x" ..
          img.height .. ", expected " .. firstImage.width .. "x" ..
          firstImage.height)
  end
  sprite:newEmptyFrame()
  sprite:newCel(layer, i, img, Point(0, 0))
end

-- Assign tags. Each spec is `name:from-to` with 1-based inclusive frame
-- numbers (Aseprite convention); the caller converts from the pipeline's
-- 0-based global indices.
if tagsParam and tagsParam ~= "" then
  for tagspec in string.gmatch(tagsParam, "([^;]+)") do
    local name, fromStr, toStr = string.match(tagspec, "^(.-):(%d+)%-(%d+)$")
    if name then
      local from, to = tonumber(fromStr), tonumber(toStr)
      if from < 1 or to > #files or from > to then
        error("import_and_tag: tag '" .. name .. "' range " .. from .. "-" ..
              to .. " out of bounds (valid 1-" .. #files .. ")")
      end
      local tag = sprite:newTag(from, to)
      tag.name = name
    else
      error("import_and_tag: malformed tag spec '" .. tagspec ..
            "' (expected name:from-to)")
    end
  end
end

-- Optional palette unification + INDEXED conversion. A shared palette
-- across every frame is the whole point of routing pawn frames through
-- Aseprite rather than the pure-Python packer.
if paletteParam and paletteParam ~= "" then
  local palette = Palette{ fromFile = paletteParam }
  sprite:setPalette(palette)
  sprite:convertColorMode(ColorMode.INDEXED)
end

sprite:saveAs(outParam)
