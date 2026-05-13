-- palette-edit-preview.lua
-- Helper for palette-edit.py. Run from a (fully-initialised) nvim that has
-- already loaded the buffer and applied the rose-pine colorscheme. Walks
-- each character, looks up the resolved treesitter highlight colour, and
-- writes a JSON dump to the path in $PALETTE_PREVIEW_OUT.
--
-- Output: an array of lines, each an array of {text, fg} spans. Whitespace
-- spans have fg = null in JSON so the caller can render them as bg.
--
-- We iterate the highlight query manually rather than using
-- `get_captures_at_pos`/`inspect_pos`, which return empty in headless mode
-- because no UI is attached to drive the highlight cache.

local out_path = os.getenv("PALETTE_PREVIEW_OUT")
if not out_path or out_path == "" then
	io.stderr:write("PALETTE_PREVIEW_OUT not set\n")
	os.exit(1)
end

local bufnr = vim.api.nvim_get_current_buf()
local ok, parser = pcall(vim.treesitter.get_parser, bufnr, "python")
if not ok or not parser then
	io.stderr:write("failed to load python treesitter parser\n")
	os.exit(2)
end
parser:parse(true)

local query = vim.treesitter.query.get("python", "highlights")
if not query then
	io.stderr:write("python highlights query not found\n")
	os.exit(3)
end

-- Build a per-character lookup: caps[row][col] = capture name.
-- Iterating in document order means later (more specific / nested) captures
-- overwrite earlier broader ones, which matches nvim's resolution order.
local caps = {}
for _, tree in ipairs(parser:trees()) do
	for id, node in query:iter_captures(tree:root(), bufnr) do
		local capture = query.captures[id]
		-- skip predicates / non-highlight captures
		if not capture:match("^_") then
			local sr, sc, er, ec = node:range()
			for r = sr, er do
				caps[r] = caps[r] or {}
				local row_start = (r == sr) and sc or 0
				local row_end = (r == er) and ec or math.huge
				for c = row_start, row_end - 1 do
					caps[r][c] = capture
				end
			end
		end
	end
end

local function hex(n)
	if not n then return nil end
	return string.format("#%06x", n)
end

local hl_cache = {}
local function hl_fg(name)
	local cached = hl_cache[name]
	if cached ~= nil then return cached or nil end
	local h = vim.api.nvim_get_hl(0, { name = name, link = false })
	local fg = hex(h.fg)
	hl_cache[name] = fg or false
	return fg
end

local normal_fg = hl_fg("Normal") or "#000000"

local function color_at(row, col)
	local cap = caps[row] and caps[row][col]
	if not cap then return normal_fg end
	-- Walk from most-specific to least-specific: "foo.bar.baz" -> "foo.bar" -> "foo"
	local name = "@" .. cap
	while true do
		local fg = hl_fg(name)
		if fg then return fg end
		local dot = name:match("()%.[^.]+$")
		if not dot then return normal_fg end
		name = name:sub(1, dot - 1)
	end
end

local lines = vim.api.nvim_buf_get_lines(bufnr, 0, -1, false)
local out = {}
for lnum, line in ipairs(lines) do
	local row = lnum - 1
	local spans = {}
	local cur_text, cur_fg = "", nil
	for col = 0, #line - 1 do
		local ch = line:sub(col + 1, col + 1)
		local fg
		if ch == " " or ch == "\t" then
			fg = vim.NIL
		else
			fg = color_at(row, col)
		end
		if fg == cur_fg then
			cur_text = cur_text .. ch
		else
			if #cur_text > 0 then
				table.insert(spans, { text = cur_text, fg = cur_fg })
			end
			cur_text, cur_fg = ch, fg
		end
	end
	if #cur_text > 0 then
		table.insert(spans, { text = cur_text, fg = cur_fg })
	end
	table.insert(out, spans)
end

local f = assert(io.open(out_path, "w"))
f:write(vim.json.encode(out))
f:close()
