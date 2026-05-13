local options = require("rose-pine.config").options
local variants = {
	main = {
		_nc = "#16141f",
		float = '#000000',
		base = "#191724",
		surface = "#1f1d2e",
		overlay = '#26233a',
		muted = "#6e6a86",
		subtle = "#908caa",
		text = "#e0def4",
		love = "#eb6f92",
		gold = "#f6c177",
		fall = '#f6a177',
		rose = "#ebbcba",
		pine = '#2e7fa0',
		moss = '#50b39c',
		foam = "#9ccfd8",
		iris = '#c4a7e7',
		highlight_low = '#302e48',
		highlight_med = '#403d52',
		highlight_high = '#56526e',
		none = 'NONE',

	},
	moon = {
		_nc = "#1f1d30",
		base = "#232136",
		surface = "#2a273f",
		overlay = "#393552",
		muted = "#6e6a86",
		subtle = "#908caa",
		text = "#e0def4",
		love = "#eb6f92",
		gold = "#f6c177",
		rose = "#ea9a97",
		pine = "#3e8fb0",
		foam = "#9ccfd8",
		iris = "#c4a7e7",
		highlight_low = "#2a283e",
		highlight_med = "#44415a",
		highlight_high = "#56526e",
		none = "NONE",
	},
	dawn = {
		_nc = "#f5f1ea",
		float = '#ffffff',
		base = "#f7f4ef",
		surface = "#fcfaf6",
		overlay = "#efeae4",
		muted = "#969491",
		subtle = "#6e6b79",
		text = "#464261",
		love = "#c20015",
		gold = "#c08200",
		fall = '#bc5028',
		rose = "#bc5d95",
		pine = "#267927",
		moss = '#599c37',
		foam = "#0a8a8a",
		iris = "#8d60c8",
		highlight_low = "#f4ede8",
		highlight_med = "#dfdad9",
		highlight_high = "#cecacd",
		none = "NONE",
	},
}

if options.palette ~= nil and next(options.palette) then
	-- handle variant specific overrides
	for variant_name, override_palette in pairs(options.palette) do
		if variants[variant_name] then
			variants[variant_name] = vim.tbl_extend("force", variants[variant_name], override_palette or {})
		end
	end
end

if variants[options.variant] ~= nil then
	return variants[options.variant]
end

return vim.o.background == "light" and variants.dawn or variants[options.dark_variant or "main"]
