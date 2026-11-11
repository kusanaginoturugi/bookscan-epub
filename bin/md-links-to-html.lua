-- Pandoc filter: point relative links at Markdown files to the HTML that
-- bin/build-docs-html.sh writes next to them.
function Link(link)
  if not link.target:match('^%a[%w+.-]*:') then
    link.target = (link.target:gsub('%.md$', '.html'):gsub('%.md#', '.html#'))
  end
  return link
end
