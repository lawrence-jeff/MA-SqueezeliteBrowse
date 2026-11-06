
--[[
=head1 NAME

applets.MaDeviceStatus.MaDeviceStatusMeta

=head1 DESCRIPTION

See L<applets.MaDeviceStatus.MaDeviceStatusApplet>.

=cut
--]]


local oo            = require("loop.simple")

local AppletMeta    = require("jive.AppletMeta")
local appletManager = appletManager


module(...)
oo.class(_M, AppletMeta)


function jiveVersion(meta)
	return 1, 1
end


function registerApplet(meta)
end


function configureApplet(meta)
	-- there is no menu entry: the applet runs in the background from the start
	appletManager:loadApplet("MaDeviceStatus")
end
