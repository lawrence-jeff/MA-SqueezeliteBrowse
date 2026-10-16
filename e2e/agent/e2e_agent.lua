-- Resident test agent for the MacroPlay applet (JiveLite and SqueezePlay): it waits for commands in @FOLDER@/e2e_cmd,
-- runs them in the UI, and writes what it did and what the screen shows to @FOLDER@/e2e_out as one line of JSON.
-- Command file, one line:  <id> <verb> [argument]      Output file:  {"id":"<id>","ok":true|false,...,"screen":{...}}
local Menu = macroRequire("jive.ui.Menu")
local Framework = macroRequire("jive.ui.Framework")
local G = macroRequire("_G")
local os = macroRequire("os")
local string = macroRequire("string")
local table = macroRequire("table")
local pcall, tostring, tonumber, type, next = G.pcall, G.tostring, G.tonumber, G.type, G.next

local FOLDER = "@FOLDER@"
local CMD, OUT = FOLDER .. "/e2e_cmd", FOLDER .. "/e2e_out"

for i = 1, 180 do
  local menu = _macroFindWidget(Menu)
  if menu and menu:getSize() >= 4 then break end
  macroDelay(1000)
end
macroDelay(2000)
pcall(function() G.jiveMain:removeItemById("macroPlay") end)

local function esc(s)
  s = tostring(s)
  s = string.gsub(s, '[%c"\\]', function(c)
    if c == '"' then return '\\"' elseif c == "\\" then return "\\\\" end
    return string.format("\\u%04x", string.byte(c))
  end)
  return '"' .. s .. '"'
end

local function enc(v)
  local t = type(v)
  if t == "table" then
    if #v > 0 or next(v) == nil then
      local parts = {}
      for i = 1, #v do parts[i] = enc(v[i]) end
      return "[" .. table.concat(parts, ",") .. "]"
    end
    local parts = {}
    for k, val in G.pairs(v) do parts[#parts + 1] = esc(k) .. ":" .. enc(val) end
    table.sort(parts)
    return "{" .. table.concat(parts, ",") .. "}"
  elseif t == "number" or t == "boolean" then
    return tostring(v)
  elseif v == nil then
    return "null"
  end
  return esc(v)
end

-- one item of the focused menu, as a table of its plain fields (text, icon, icon-id, id, type, style ...)
local function item_at(menu, i)
  local item
  if menu.getItem then
    local ok, it = pcall(menu.getItem, menu, i)
    if ok then item = it end
  end
  if type(item) ~= "table" and menu.list and menu.list.db and menu.list.db.item then
    local ok, it = pcall(menu.list.db.item, menu.list.db, i)
    if ok then item = it end
  end
  if type(item) ~= "table" then return nil end
  local row = {}
  for _, key in G.ipairs({ "text", "icon", "icon-id", "id", "type", "style", "textkey", "homeMenuText" }) do
    local v = item[key]
    if type(v) == "table" and key ~= "icon" then
      -- a localized string and the like: its text
      local okT, t = pcall(tostring, v)
      if okT and type(t) == "string" and not string.match(t, "^table: ") then v = t end
    end
    if type(v) == "string" or type(v) == "number" or type(v) == "boolean" then row[key] = v end
  end
  return row
end

-- what the window shows: its title, the menu with its items and selection, how many windows are open
local function screen()
  local window = Framework.windowStack[1]
  local info = { windows = #Framework.windowStack }
  window:iterate(function(w)
    if w.getWidget and not info.title then
      local ok, t = pcall(function() return w:getWidget("text"):getValue() end)
      if ok and type(t) == "string" then info.title = t end
    end
  end)
  local menu = _macroFindWidget(Menu)
  if menu then
    info.menu_size = menu:getSize()
    info.menu_index = menu:getSelectedIndex()
    local items = {}
    for i = 1, G.math.min(info.menu_size or 0, 200) do items[i] = item_at(menu, i) or {} end
    info.items = items
    local sel = items[info.menu_index]
    info.selected = sel and sel.text
  end
  return info
end

local function pick(pattern)
  local menu = _macroFindWidget(Menu)
  if not menu then return false end
  for i = 1, menu:getSize() do
    menu:setSelectedIndex(i)
    macroDelay(100)
    if macroIsMenuItem(pattern) then return true end
  end
  return false
end

local function lua_pattern(label)
  return "^" .. string.gsub(label, "([%%^$().%[%]*+%-?])", "%%%1")
end

-- wait until the window and the menu stop changing (a window or its rows that are still loading change between looks)
local function signature()
  local info = screen()
  local first = info.items and info.items[1]
  return table.concat({ info.windows, info.title or "", info.menu_size or "", info.menu_index or "", first and first.text or "" }, "|")
end

local function settle(min_ms, max_ms)
  macroDelay(min_ms or 400)
  local last, stable = signature(), 0
  local waited = min_ms or 400
  while waited < (max_ms or 6000) and stable < 2 do
    macroDelay(250)
    waited = waited + 250
    local now = signature()
    if now == last then stable = stable + 1 else stable = 0; last = now end
  end
end

local function scroll(n)
  local menu = _macroFindWidget(Menu)
  if not menu then return false end
  if n == 0 then return true end
  local target = (menu:getSelectedIndex() or 1) + n
  local size = menu:getSize()
  if target < 1 then target = 1 end
  if target > size then target = size end
  menu:setSelectedIndex(target)
  macroDelay(120)
  return true
end

local ACTIONS = { long_press = "add", queue = "go_playlist" }

local value
local function run(verb, arg)
  if verb == "ping" then return true
  elseif verb == "dump" then return true
  elseif verb == "home" then macroHome(600, 900); return true
  elseif verb == "action" then macroAction(tonumber(arg and arg:match("^(%d+)%s")) or 900, (arg:gsub("^%d+%s+", ""))); return true
  elseif verb == "go" then macroAction(100, "go"); settle(700); return true
  elseif verb == "back" then macroAction(100, "back"); settle(500); return true
  elseif verb == "long_press" then macroAction(100, "add"); settle(1500, 8000); return true
  elseif verb == "queue" then macroAction(100, "go_playlist"); settle(1500, 8000); return true
  elseif verb == "pick" then return pick(lua_pattern(arg))
  elseif verb == "open" then
    if not pick(lua_pattern(arg)) then return false end
    macroAction(100, "go"); settle(700); return true
  elseif verb == "openid" then
    -- open the home/menu entry with this id (entries such as My Music have no text of their own on the home menu)
    local menu = _macroFindWidget(Menu)
    if not menu then return false end
    for i = 1, menu:getSize() do
      local row = item_at(menu, i)
      if row and row.id == arg then
        menu:setSelectedIndex(i); macroDelay(150)
        macroAction(100, "go"); settle(700)
        return true
      end
    end
    return false
  elseif verb == "nth" then
    local menu = _macroFindWidget(Menu)
    local n = tonumber(arg)
    if not menu or menu:getSize() < n then return false end
    menu:setSelectedIndex(n); macroDelay(150); return true
  elseif verb == "scroll" then return scroll(tonumber(arg) or 0)
  elseif verb == "do" then
    -- a sequence in one command: a:<action> runs a UI action and settles, s:<n> moves the selection by n, w:<ms> waits
    for token in string.gmatch(arg, "%S+") do
      local kind, val = token:match("^(%a):(.+)$")
      if kind == "a" then macroAction(100, val); settle(500)
      elseif kind == "s" then if not scroll(tonumber(val) or 0) then return nil, "no menu for " .. token end
      elseif kind == "w" then macroDelay(tonumber(val) or 200)
      else return nil, "bad token " .. token end
    end
    return true
  elseif verb == "wait" then macroDelay(tonumber(arg) or 500); return true
  elseif verb == "shot" then
    -- the whole screen: every open window from the bottom up (a menu or popup is drawn over the window under it)
    macroDelay(900)
    local drawn = pcall(function()
      local ui = G.jive.ui
      local Surface = macroRequire("jive.ui.Surface")
      local w, h = Framework:getScreenSize()
      local surface = Surface:newRGB(w, h)
      local stack = Framework.windowStack
      local bottom = 1
      for i = 1, #stack do
        if not stack[i].transparent then bottom = i; break end
      end
      for i = bottom, 1, -1 do stack[i]:draw(surface, ui.LAYER_FRAME | ui.LAYER_CONTENT) end
      surface:saveBMP(FOLDER .. "/" .. arg .. ".bmp")
    end)
    if not drawn then return macroScreenshot(200, arg) end  -- the top window only
    return true
  elseif verb == "eval" then
    -- run a Lua file (relative to the folder) in this environment; what it returns goes into "value"
    local f, err = G.loadfile(FOLDER .. "/" .. arg)
    if not f then return nil, tostring(err) end
    G.setfenv(f, G.getfenv(1))
    value = f()
    return true
  end
  return nil, "unknown verb " .. tostring(verb)
end

local function reply(id, ok, note, quit)
  local result = { id = id, ok = ok and true or false, note = note, value = value }
  local okS, scr = pcall(screen)
  result.screen = okS and scr or { error = tostring(scr) }
  local f = G.io.open(OUT .. ".tmp", "w")
  if f then f:write(enc(result)); f:close(); os.rename(OUT .. ".tmp", OUT) end
end

reply("ready", true, "agent started")
while true do
  local f = G.io.open(CMD, "r")
  if f then
    local line = f:read("*l"); f:close()
    os.remove(CMD)
    if line then
      local id, verb, arg = line:match("^(%S+)%s+(%S+)%s*(.*)$")
      if verb == "exit" then reply(id, true, "exiting"); break end
      -- no pcall here: the commands yield (macroDelay), which a pcall cannot cross
      value = nil
      local res, err = run(verb, arg)
      reply(id, res == true, err)
    end
  end
  macroDelay(150)
end
