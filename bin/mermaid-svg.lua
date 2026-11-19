-- Replace ```mermaid code blocks with the SVG that mermaid-cli (mmdc) draws,
-- inlined, so the HTML shows the chart without JavaScript. Without mmdc the
-- block stays as code and a warning is printed.
local count = 0

function CodeBlock(block)
  if not block.classes:includes('mermaid') then
    return nil
  end
  count = count + 1
  local ok, svg = pcall(pandoc.pipe, 'mmdc',
    {'--quiet', '-i', '-', '-o', '-', '-e', 'svg', '-b', 'transparent', '-I', 'mermaid-' .. count},
    block.text)
  if not ok then
    io.stderr:write('mermaid-svg.lua: mmdc failed, the chart stays as code: ' .. tostring(svg) .. '\n')
    return nil
  end
  -- The SVG fills its figure (width="100%"). Pandoc's text column (36em) would
  -- shrink a large chart's text to unreadable, so the figure leaves the column:
  -- as wide as the window, up to the chart's own width, centred on the page.
  local width = svg:match('viewBox="[%d%.%-]+ [%d%.%-]+ ([%d%.]+)') or '1000'
  local style = string.format('width: min(96vw, %spx); max-width: none; position: relative; '
    .. 'left: 50%%; transform: translateX(-50%%); margin: 1em 0', width)
  return pandoc.RawBlock('html', '<figure class="mermaid" style="' .. style .. '">' .. svg .. '</figure>')
end
