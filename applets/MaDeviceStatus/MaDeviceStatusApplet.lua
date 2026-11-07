
--[[
=head1 NAME

applets.MaDeviceStatus.MaDeviceStatusApplet - Report the device's memory, uptime and battery to Music Assistant

=head1 DESCRIPTION

Every minute the applet reads the numbers the Diagnostics applet shows on the Radio's screen (memory in use, uptime, the
wireless signal and the battery: charge, charger state and temperature) and sends them to the server with the request
"madevice". Music Assistant adds them to the player's status as ma_* fields. A device without the Radio's power chip (a
piCorePlayer) reports no battery, and one without a wireless interface no signal. Nothing is sent while the server is not
connected.

=cut
--]]


local io, ipairs, jnt, math, pcall, tonumber = io, ipairs, jnt, math, pcall, tonumber

local oo            = require("loop.simple")
local string        = require("string")
local table         = require("table")

local Applet        = require("jive.Applet")
local log           = require("jive.utils.log").logger("applet.MaDeviceStatus")
-- not in JiveLite, which has no wireless interface of its own to ask
local _, Networking = pcall(require, "jive.net.Networking")
local Timer         = require("jive.ui.Timer")
local appletManager = require("jive.AppletManager")


module(...)
oo.class(_M, Applet)


-- how often to report, and how soon after the start (the server may not be connected yet)
local INTERVAL = 60000
local FIRST = 20000
-- the Radio's power chip
local POWER = "/sys/devices/platform/i2c-adapter:i2c-1/1-0010/"


local function _read(path)
	local f = io.open(path)
	if not f then
		return nil
	end
	local text = f:read("*all")
	f:close()
	return text
end


local function _number(path)
	local text = _read(path)
	return text and tonumber(string.match(text, "%-?%d+%.?%d*"))
end


-- the values to report, as "key:value" strings; the same sums as the Diagnostics applet
local function _collect()
	local values = {}

	local uptime = _number("/proc/uptime")
	if uptime then
		values[#values + 1] = "up:" .. math.floor(uptime)
	end

	local meminfo = _read("/proc/meminfo")
	if meminfo then
		local function field(name)
			return tonumber(string.match(meminfo, name .. ":%s+(%d+)"))
		end
		local total, free, buffers, cached = field("MemTotal"), field("MemFree"), field("Buffers"), field("Cached")
		if total and free and buffers and cached and total > 0 then
			values[#values + 1] = "mem:" .. math.ceil((total - (free + buffers + cached)) / total * 100)
		end
	end

	-- the wireless signal in percent, as the Diagnostics applet shows it; a wired device has no wireless interface, and a
	-- failure here must not keep the other values from being sent
	local ok, signal = pcall(function()
		local wlan = Networking and Networking:wirelessInterface(jnt)
		return wlan and wlan:getSignalStrength()
	end)
	if ok and signal then
		values[#values + 1] = "sig:" .. signal
	elseif not ok then
		log:warn("no wireless signal: ", signal)
	end

	local charger = _number(POWER .. "charger_state")
	if charger then
		values[#values + 1] = "chg:" .. charger
		-- 1 is "no battery installed"
		if charger ~= 1 then
			local charge, capacity = _number(POWER .. "battery_charge"), _number(POWER .. "battery_capacity")
			if charge and capacity and capacity > 0 then
				values[#values + 1] = "batt:" .. math.min(100, math.floor(charge * 100 / capacity + 0.5))
			end
			local temperature = _number(POWER .. "battery_temperature")
			if temperature then
				values[#values + 1] = "temp:" .. string.format("%.1f", temperature / 32)
			end
		end
	end

	return values
end


function _report(self)
	local player = appletManager:callService("getCurrentPlayer")
	local server = player and player:getSlimServer()
	if not server or not server:isConnected() then
		log:debug("no connected server, nothing sent")
		return
	end

	local ok, values = pcall(_collect)
	if not ok then
		log:warn("could not read the device status: ", values)
		return
	end
	if #values == 0 then
		return
	end

	local request = { "madevice" }
	for _, value in ipairs(values) do
		request[#request + 1] = value
	end
	log:debug("sending: ", table.concat(request, " "))
	server:userRequest(nil, player:getId(), request)
end


function init(self)
	log:info("reporting the device status every ", INTERVAL / 1000, " seconds")
	self.timer = Timer(INTERVAL, function() self:_report() end)
	self.timer:start()
	self.first = Timer(FIRST, function() self:_report() end, true)
	self.first:start()
end
