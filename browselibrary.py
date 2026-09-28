"""
BrowseLibraryHandler - the JiveLite integration's browselibrary/menu command
handler. Wires into the real Music Assistant library (self.mass.music.artists
/ .albums / .tracks / .playlists / .radio / .podcasts / .audiobooks - the
MediaControllerBase-derived controllers documented in
music_assistant/controllers/music/README.md) for
artists/albums/tracks/playlists/radio/podcasts/audiobooks listings,
replacing the Stage 1 hardcoded test data.

Response shapes (base.actions, windowStyle differences between the
artist-filtered and unfiltered album views, icon/icon-id conventions,
textkey/presetParams, pagination) are ported directly from the verified
standalone scaffold and cross-checked against jivelite-boot-sequence-analysis.md.

Data source notes (verified against the real music_assistant /
music_assistant_models source, not inferred):
  - Top-level listings (no artist_id/album_id filter) use each controller's
    own library_items(limit=, offset=, summary=False)/library_count() - MA
    paginates these server-side.
  - "Albums for an artist" and "tracks for an album" use the dedicated
    relationship methods (ArtistsController.albums(artist_id, "library"),
    AlbumsController.tracks(album_id, "library", in_library_only=True))
    instead - these are documented as NOT taking limit/offset (they return
    the full related set), so we paginate that result ourselves with the
    same _paginate() helper Stage 1 used for its in-memory lists.
  - item_id is typed as str on the dataclasses but is genuinely handled as
    both str and int in several places in MA's own source (e.g.
    remove_item_from_library's `int(item_id)`) - every id we hand back to
    the client is str()-wrapped defensively rather than assumed to already
    be a string.
  - Playlist tracks are the one exception to the "relationship method
    returns a plain unpaginated list" pattern above: PlaylistController.
    tracks() is an async generator (playlist tracks aren't stored in MA's
    db - always fetched, cached, from the provider), and the yielded items
    aren't guaranteed to be MA library items at all, unlike an album's
    tracks - see get_playlist_tracks()'s own docstring for the full
    reasoning and what that changes about how playback is wired for them.

NOT yet handled (left for later, same as before):
  - Full home menu parity with real LMS (Favorites, Settings,
    plugin-contributed entries) - these map to entirely separate MA
    subsystems, each its own piece of work, not something to add here
    without review. Only a "My Music" node with what we've actually
    implemented (Artists, Albums, Tracks, Playlists, Audiobooks,
    Podcasts, Radio, Search) is added; nothing links to a mode we can't
    handle, to avoid silent dead ends. This ordering deliberately matches
    MA's own root UI taxonomy, not real LMS's menu structure - a
    deliberate choice made once enough of MA's content model was wired
    up to make that the more honest fit (see MY_MUSIC_NODE's own notes
    for the reasoning). "Album Artists" and "All Artists" used to be two
    separate tiles here, modeling a role distinction our data never
    actually had - collapsed into one real "Artists" tile. Genres was
    dropped entirely (not just left unimplemented) since MA doesn't
    model it as a first-class browsable controller the way it does
    everything else here - more like a tag/filter on albums and tracks -
    and there's no near-term plan to build that out.
  - Playback (playlistcontrol) - only cmd=load is wired up
    (BrowseLibraryHandler._handle_playlistcontrol, using
    mass.player_queues.play_media), via either a track_id (album tracks)
    or a uri (playlist tracks). add/insert, and album_id/artist_id/
    playlist_id-driven playlistcontrol (playing a whole album/artist/
    playlist rather than one track), aren't handled yet - see that
    method's own docstring for the full scope.
  - Real ALBUM and ARTIST art are both wired up now (see the "Icon / cover
    art serving" section further down and _fetch_real_item_art's own
    docstring for the icon_id namespacing that keeps the two from
    colliding). Chrome icons (My Music, category icons) use real files
    from static/ (see STATIC_DIR/_resolve_static_icon_path further down)
    rather than placeholders too - the solid-color placeholder is only a
    last-resort fallback now, for something genuinely not found either
    way.
  - The artist/album favorites_url values below still use LMS's own "db:"
    query convention (kept for fidelity, ported from the scaffold's
    real-LMS-verified fields) rather than anything MA's squeezelite
    provider necessarily understands if a user actually tries to save one
    as a preset against MA - untested against MA specifically, flagged as
    a follow-up rather than guessed at here. Track favorites_url, by
    contrast, now uses the track's own MA-native `.uri` (e.g.
    "library://track/42"), which MA's own provider stack already
    understands, so that one link is real.
"""

# browselibrary.py v58 | 2026-09-21 | Fixed a real, confirmed bug in
# _handle_playlist - not a guess, a real live traceback: "Remove from
# Playlist" (the v57 fix having gotten the context menu itself to
# finally render and be selectable, for the first time reaching this
# code path at all) raised "TypeError: 'NoneType' object can't be
# awaited" from "await self.mass.player_queues.delete_item(...)". The
# real current upstream source (controllers/player_queues/
# controller.py) confirms delete_item is a plain "def", not "async
# def", returning None directly. The same reference source shows
# move_item and clear as equally plain "def" - but clear() was already
# independently confirmed working via a real device test, which a bare
# "await <plain sync None>" could not do (that raises the same
# TypeError unconditionally, no exceptions) - a real contradiction,
# meaning the exact deployed MA version may not exactly match the
# reference source just checked. Added _maybe_await() (checks
# inspect.isawaitable() before awaiting) and routed play_index,
# delete_item, move_item, and clear through it, rather than betting on
# which of the two per-method possibilities is actually true for this
# deployment.
# (v57 was: v56's track_id fix, while genuinely
# correct (confirmed real LMS uses a bare numeric id, not a uri, and
# it's now sent that way), did NOT fix the actual symptom - a real
# device retest showed the identical "Hiding NP child window"/"Popped"
# behavior with the fix in place. Found the real cause via a
# line-by-line comparison of that failing trace against the earlier
# working (real LMS) one: right after "_actionHandler(more): json
# action", the working trace logs "Context Menu" / "_newWindowSpec()" /
# "_newDestination():"; the failing one skips straight to
# "_performJSONAction(from:nil, qty:nil)" - the entire Context Menu
# branch never ran. Traced to menu_item_from_media_details()
# (aioslimproto's own function) unconditionally setting
# details["nextWindow"] = "nowPlaying" at its very end, OUTSIDE its own
# include_actions check - missed even after v53 switched to
# include_actions=False, since that only skips the actions dict, not
# this separate unconditional line. Real LMS's own queue rows never
# carry a row-level nextWindow at all (confirmed via multiple real
# captures) - the client appears to fall back to a row's own nextWindow
# regardless of which action fired, sending every interaction straight
# to _goNowPlaying() instead. Popped from the row in
# _build_queue_item_loop, alongside the existing style/type handling.
# (v56 was: Fixed the real root cause of the
# queue context-menu not appearing, found via a direct, side-by-side
# comparison of a real LMS client trace against our own for the exact
# same interaction (long-press a queue row) - not inferred, the actual
# deciding piece of evidence after many rounds of narrowing. Real LMS's
# outgoing contextmenu request carries track_id:26566 (a bare numeric
# database id); ours carried track_id:library://track/25 (the full uri)
# - the one concrete difference between a request that correctly
# reached _browseSink()/"Pushed" on real LMS's own client trace, and
# one that instead hit "Hiding NP child window"/"EVENT_WINDOW_POP"/
# "Popped" on this server, every single time this was tested, despite
# every other part of the chain (server computing correct data,
# serializing it, delivering the exact bytes, client receiving and
# invoking its own callback) being independently confirmed correct in
# earlier rounds. _build_queue_item_loop's metadata["item_id"] was
# wrongly set to the full uri (a real mistake, not questioned until
# this comparison) instead of track.item_id (the real bare numeric
# field this project already reads directly off the same track object
# for everything else in this loop) - fixed to use the real field, uri
# only as a fallback for a queue item that genuinely has no item_id.
# (v55 was: Diagnostic-only addition, no
# functional change: _handle_contextmenu now proactively round-trips
# its own response through json.dumps before returning, logging the
# result (or the failure) - added alongside matching diagnostic logging
# in aioslimproto's cli.py (a separate patch to that file) to trace
# exactly where a queue-context-menu response goes after this function
# returns it, since a real device test showed the response being
# computed correctly but never reaching the screen, with no exception
# anywhere and no explanation found yet. Reverted the earlier v54-era
# timing-fix patch to cli.py first (a real device test showed no change
# in behavior, just added latency, ruling that theory out) before
# adding this new diagnostic pass in its place.
# (v54 was: Two real fixes from a real device
# log, both confirming v53's core mechanism actually works now
# (contextmenu requests reached _handle_contextmenu correctly both
# times, got real non-empty content back) but two things still broke
# the visible result: (1) the response's own "window" field was
# {"isContextMenu":1} in both _handle_contextmenu and _handle_trackinfo -
# wrong, confirmed via a direct re-check of the real pcap capture: the
# real response's window is {"windowStyle":"text_list"}. isContextMenu:1
# IS real, but belongs on the ACTION that navigates here (base.actions.
# more's own "window" field, already correct/untouched), not on the
# response data itself - a real, previously-unnoticed mixup, fixed in
# both handlers now. (2) the same real log showed a genuine regression
# from v53's row-stripping fix: JiveLite's own implicit single-tap-to-
# play on a style:itemplay queue row sends "playlist index <N>", not
# "playlist jump <N>" - neither aioslimproto's built-in (only relative
# "index +1") nor this handler covered plain "index <N>" before, so
# single-tap-to-play on a queue row was silently broken from the moment
# row actions were removed. Added "index" as a real alias for "jump" in
# _handle_playlist (same underlying play_index() call, confirmed via
# the same real log, not guessed).
# (v53 was: Rebuilt the queue context-menu
# feature against real ground truth - a genuine pcap capture of LMS
# 9.1.1 handling this exact interaction (long-press a queue row, pick
# Remove from playlist), not inference from slimserver/JiveLite source
# reading alone. Real findings, several correcting earlier versions:
# (1) real queue rows carry NO "actions" field at all - not go, not
# add, not more, nothing - just style/text/track/album/artist/params/
# icon; single-tap-to-play still works against real LMS with zero
# per-row actions, so JiveLite's tap handling for style:itemplay rows
# must derive play-this-index from context, not a literal action
# lookup. _build_queue_item_loop now matches this exactly
# (include_actions=False, explicit style:itemplay, params trimmed to
# just track_id+playlist_index) instead of v51's partial "more"-only
# pop. (2) base.actions.more has no "addAction" key in the real capture
# - v52's base["addAction"]="more" addition contradicted this and is
# removed; with rows carrying zero actions there's nothing left to
# shadow the base action anyway. (3) The real context menu's per-row
# shape: the SAME command repeated under ALL FOUR action keys (play/
# go/add/add-hold), plus addAction:"go" and type:"text" on every row -
# confirmed exactly for "Remove from playlist" (-> playlist delete
# <idx>) and "Play" (-> playlist jump <idx>, style:itemplay). Real
# order is Remove/Play Next/Play, then Save to Favorites and generic
# drill-downs (Album Artist, Album, Genre, ...) - only the first three
# built here per direct instruction; "Play Next" itself wasn't present
# in the captured pcap (condition-dependent - not shown for the tested
# track/position), so its exact command is inferred from the same
# all-four-keys pattern the other two rows prove, using playlist move
# <idx> (pos_shift=0) exactly as already implemented.
# (v52 was: Fixed the real root cause of
# long-press on a queue row just duplicating the track instead of
# showing a menu - confirmed via a real user report that the exact same
# JiveLite client, pointed at a real LMS server instead of this one,
# shows the real context menu (Remove From Playlist/Play/Save To
# Favorites) on the identical long-press gesture, proving the gesture
# and client-side logic were never the problem. Traced into the real
# JiveLite client source (ralph-irving/jivelite): Menu.lua's real
# EVENT_MOUSE_HOLD handler unconditionally does Framework:pushAction
# ("add") on a long-press (a commented-out "_showContextMenuAction"
# call sits right above it, confirming this used to work differently);
# SlimBrowserApplet.lua's own _actionHandler only treats that "add"
# action as a context-menu trigger when item['addAction'] == 'more' OR
# base.addAction == 'more' - its own comment names this exact mechanism
# "temporary... backwards compatibility... until all 'add' commands are
# removed in SC in favor of 'more'". Real LMS sends this flag; this
# server never did. Added base["addAction"] = "more" to the queue
# status response - aioslimproto's own built-in base doesn't set it
# either, so it's added here, scoped to the queue view specifically.
# v51's row["actions"].pop("more", ...) stays - still correct on its
# own terms (a stale row-level "more" pointing at a literal add would
# still be wrong if ever invoked), just not what was actually blocking
# this.
# (v51 was: Fixed a real device-confirmed bug:
# tapping "Clear Playlist" went straight to a blank screen with ZERO
# trace of any request reaching the server (no [BL] print at all, while
# ordinary heartbeat traffic kept flowing normally through the same
# moment) - meaning the failure was purely client-side rendering, before
# JiveLite ever got far enough to send anything. Root cause, confirmed
# via a direct re-check of the real source (_addJivePlaylistControls,
# Slim/Control/Queries.pm): the outer "Clear Playlist" row there sets
# text/icon-id/offset/count/item_loop and nothing else - v50 added
# "type": "playlist" to this row without that being in the real source,
# an unverified guess. Removed to match the real shape exactly.
# (v50 was: Added real queue-management
# actions, built entirely from real source rather than guessed - the
# actual slimserver source (Slim/Control/Queries.pm, Slim/Menu/
# TrackInfo.pm), confirmed against aioslimproto's own real source (which
# implements none of this - only "playlist index +1"), and MA's real
# PlayerQueuesController (play_index/delete_item/move_item/clear, each
# signature confirmed before use). Three real, previously-entirely-
# unhandled commands added: "contextmenu" (a queue row's long-press -
# was raising NotImplementedError for every long-press on every queue
# row until now, confirmed neither aioslimproto nor this file had ever
# implemented it), "playlist" jump/delete/move/clear (aioslimproto's own
# built-in only implements index+1), and "jiveblankcommand" (real LMS's
# own client-side Cancel no-op). New per-row "Play Song"/"Remove from
# Playlist"/"Play Next" context menu (skip logic matches real LMS
# exactly - hide Play Song if already playing that row, hide Play Next
# if already current/next). New "Clear Playlist" row appended at the
# real tail of the queue view, with the same real two-step Cancel/
# Confirm inline submenu real LMS uses, count bumped by 1 to match.
# _build_queue_item_loop rows now carry playlist_index in their own
# params (real LMS's own _addJiveSong convention) - required so a
# specific row's long-press can be tied back to its real queue position.
# Extracted the existing two-push queue-update logic (v36-v39) into a
# shared _push_queue_update(), now reused by playlistcontrol add/insert
# AND all four new playlist subcommands, instead of duplicating it.
# Caught and fixed one real design mistake mid-implementation: move_item's
# pos_shift=0 has a specific documented meaning ("move to front of
# upcoming items") that's exactly "Play Next" and already handles real
# buffer-boundary edge cases internally - simplified away an unnecessary,
# riskier hand-computed target index in favor of it. NOT yet verified
# against a real device test. Needs a real playlistclear_225x225_m.png
# (or playlistclear.png fallback) placed in static/ - not included here.
# (v49 was: Naming consistency cleanup, no
# behavior change beyond the mode string rename described below. Audited
# the whole file for naming drift (function names, dispatch mode
# strings, parameter names) after a real report flagged get_all_
# favorites vs get_search_all reading inconsistently. Found two real
# mismatches, both fixed: (1) get_all_favorites -> get_favorites_all,
# matching get_search_all's own word order, so both "combine across all
# seven media types" functions now follow one rule: get_<category>_all()
# / mode "<category>_all"; (2) mode "searchall" -> "search_all" (both the
# emitting _search_category_menu() entry and the _dispatch listener
# updated together) - it was the only mode string in the whole dispatch
# table not underscore-separated, while its own sibling "favorites_all"
# already was. Audited favorite_only (59 uses, no favorites_only/is_
# favorite drift found), search param naming, and index/quantity naming
# for the same kind of drift - none found; this was the one real case.
# (v48 was: Fixed a real report: some albums
# (Spotify ones, specifically noticed) showed fine in browse - real art,
# real title, since the ALBUM itself has a local library row - but
# selecting the album to see its tracks showed nothing. Root cause,
# confirmed against the real music-assistant/server source
# (controllers/music/media/albums.py), not a log-guess: get_tracks()
# called AlbumsController.tracks(album_id, "library", in_library_only=
# True), which returns ONLY tracks that separately have their own local
# library row - favoriting/adding an album does not guarantee every
# individual track also got its own local row, so an album that only got
# that partial treatment returned an empty or partial track list.
# Switched to in_library_only=False, which additionally reaches out live
# to the actual provider (Spotify, etc.) and merges in whatever tracks
# aren't mirrored locally yet - confirmed in the same source as the real,
# intended fallback for exactly this case. Real tradeoff: an affected
# album's response now includes a live provider round-trip instead of
# being purely local-DB, so it can be measurably slower for exactly the
# albums this fixes - accepted, since no tracks at all is worse.
# (v47 was: Gave the Favorites home-menu tile
# a real icon - "html/images/favorites.png" in the tile definition,
# matching every sibling tile's plain unsized-name convention exactly
# (AllArtists.png, not AllArtists_225x225_m.png). Requires a real file
# named favorites_225x225_m.png (or favorites.png as a fallback) placed
# in static/ - _resolve_static_icon_path already handles the rest, no
# code changes needed there.
# (v46 was: Fixed a real visual bug reported
# from the device: Search All and All Favorites both rendered their
# two-line "text" (title\nartist) as two equal-size lines, looking
# visibly "off" compared to the real Tracks view's title+smaller-
# subtitle treatment. Root cause: both used windowStyle "text_list";
# get_all_tracks/get_albums use "icon_list" for the same two-line
# convention, and that's what actually gives the subtitle treatment.
# Switched both to "icon_list" - already proven correct elsewhere in
# this file for exactly this row mix (two-line AND single-line rows,
# each already carrying its own "icon" field), so nothing else needed
# to change.
# (v45 was: Added a real Favorites browse tree,
# per direct discussion - not a new subsystem, just a real favorite=/
# favorite_only= filter every media controller's library_items()/
# library_count() already supports (confirmed against the real
# music-assistant/server source on GitHub, controllers/music/media/*.py),
# threaded through the same seven get_X() functions and the same
# _dispatch branches every other browse already goes through. New:
# "Favorites" home-menu tile -> _favorites_category_menu() (All
# Favorites/Artists/Albums/.../Radio, mirrors _search_category_menu()
# exactly) -> per-type entries reuse the existing get_X(favorite_only=
# True) calls; "All Favorites" is get_favorites_all(), which - unlike
# get_search_all()'s fixed per-type cap - does GENUINE pagination across
# the combined set: real per-type counts via library_count(favorite_
# only=True) (a real total, not a len()-based approximation - unlike
# search, favorite_only genuinely supports library_count()), then only
# fetches whichever type(s) the requested window actually overlaps,
# concurrently. A normal page overlaps one type, occasionally two at a
# boundary - never all seven unless the window itself is that wide.
# (v44 was: Documentation-only change - no
# behavior difference from v43. Learned that "never hand the client a
# real/proxy image URL, only local identifiers this server resolves
# itself" was already a deliberate architectural decision from a prior
# session, not something v42/v43 invented: it's what lets this code stay
# in the middle of every image fetch, e.g. to serve a smaller/adjusted
# icon to a low-power device in the future - flexibility that's lost the
# moment a client can resolve a URL on its own. v40/v41 violated this
# rule without anyone involved realizing it was already settled, which is
# what actually caused the "some queue rows have no art" bug. Added an
# explicit banner comment at the top of the icon-serving section stating
# this rule plainly, so a future session doesn't rediscover it the same
# way - by regressing it first.
# (v43 was: Closed a remaining leak v42
# missed: the "no album" edge case in _build_queue_item_loop still
# called get_image_url_for_item() directly and handed whatever it
# returned straight to the client - the exact same mistake v42 fixed
# for the common (has-an-album) case, just left in place for this
# narrower one. Confirmed via direct instruction: this server must
# never hand the client a resolved URL (raw external or proxy-shaped)
# in any code path - only local music/{id}/cover-style identifiers,
# resolved server-side on request. Audited every other "icon"/image_
# url assignment in this file for the same mistake (grepped every
# get_image_url/get_image_url_for_item call) - nothing else found;
# _fetch_real_item_art's own use of these is fine, since it only
# consumes the result server-side to fetch real bytes for its HTTP
# response, never exposing it to the client as a URL. No local route
# exists for a bare track id (_fetch_real_item_art's own icon_id
# scheme treats an unprefixed numeric id as an ALBUM, not a track), so
# this edge case now just leaves the icon blank rather than leaking
# anything - a client-visible missing image is the correct, safe
# outcome here, not a resolved URL.
# (v42 was: Found the real root cause of
# missing queue-row art, via v41's own diagnostic print plus a real
# [ICON] UNMATCHED capture landing in the same window as a failing
# track (New Truck): "GET /imageproxy/https://r2.theaudiodb.com/.../
# image_90x90_m". v40/v41 both eagerly resolved a real image_url here
# (via _pick_best_image + get_image_url/get_image_url_for_item) and
# handed it directly to the device. When the best-ranked image is
# remotely-hosted - which _pick_best_image deliberately prefers - the
# device/aioslimproto wraps that raw URL in a local
# "/imageproxy/<url>/image_WxH_m" request expecting this server to
# fetch and proxy it, a route never implemented here. New Truck's own
# album art is proven working via the real /music/{id}/cover route in
# the very same capture - confirming the resolution logic itself was
# fine, the delivery mechanism was the problem. Real fix: stopped
# resolving a URL here at all. Queue rows now just point at that same
# already-working local route (f"music/{id}/cover", the exact format
# every other real icon in this file already uses - get_albums,
# get_playlists, etc.) - handle_icon already does the real resolution,
# remote-image proxying included, lazily and server-side when the
# device actually requests it. No second, eager, URL-based resolution
# path needed for queue rows specifically - that second path was
# exactly what broke.
# (v41 was: v40 wasn't enough - a real device
# test showed a track whose album has real art (confirmed serving fine
# via the direct /music/{id}/cover route in the same capture) still had
# no art on its queue row, still triggering the client's bad Folder.jpg
# fallback request. v40 only checked the track's OWN images list before
# falling back to get_image_url_for_item(); most tracks don't carry
# their own art at all - it's the album's. Added a real fetch of the
# fully-hydrated Album (mass.music.albums.get_library_item(), the same
# pattern _fetch_real_item_art already uses for the direct icon route)
# when the track's own images list is empty, before falling back to
# get_image_url_for_item() - theory being that a queue item's
# track.album is often just a lightweight ItemMapping reference without
# its own metadata.images, which get_image_url_for_item's internal
# fallback chain may need hydrated to actually find anything. Added a
# real diagnostic print (which path resolved art, or didn't, per row)
# since inferring success from whether a bad request showed up
# afterward isn't precise enough - confirmed by v40 itself looking
# right on paper but still failing this specific case.
# (v40 was: Fixed missing album art on some
# queue-view rows, reported via a real device test as unmatched
# GET requests with raw, unescaped artist/album folder names
# (e.g. "GET /Jimmy Buffett/All the Great Hits/Folder_225x225_m.jpg") -
# real LMS's own client-side fallback when a track's icon URL comes back
# empty, guessing a raw filesystem cover path instead. These fail at
# the aiohttp parser level before ever reaching this server's routing
# (unescaped spaces break the HTTP request line outright), so they
# can't be served even in principle - the real fix is making the
# server-sent image_url non-empty in the first place. Root cause:
# _build_queue_item_loop (v35) resolved art via a bare
# get_image_url_for_item() call, skipping the same _pick_best_image()
# ranking _fetch_real_item_art already uses for the real /music/{id}/
# cover icon route - that ranking is what actually finds a real
# standalone cover file over embedded-in-audio art, and skipping it is
# a plausible reason some tracks came back with nothing. Queue rows now
# go through the same real resolution chain: _pick_best_image() over
# the track's own images list first, falling back to
# get_image_url_for_item() only when the item has no images list at
# all - matching _fetch_real_item_art exactly rather than a second,
# weaker implementation.
# (v39 was: Fixed a real off-by-one bug in
# v38's new push that made it crash on every single add/insert -
# confirmed via a real traceback (ValueError: invalid literal for
# int() with base 10: '-'). sub["data"]["request"][1] is the FULL
# inner array INCLUDING the command name ('status'), not just the
# offset/limit args on their own - v38 read sub_args[0] as the offset
# when it was actually the literal string 'status', then tried
# int()-ing sub_args[1] (the real offset, '-') as if it were the
# limit. The add itself always worked (queue genuinely grew every
# time, confirmed via real_total in the same captures) but the crash
# meant playlistcontrol itself reported an error back to the device on
# every add, and the new push never completed successfully even once
# - so v38 was never actually tested, not merely "not confirmed yet."
# Fixed by slicing off the command name (sub["data"]["request"][1][1:])
# before reading offset/limit, matching the same [offset, limit, ...]
# shape _handle_queue_status's own args already use.
# (v38 was: Fixed the queue-view LIST not
# updating after add/insert, even though a real device test confirmed
# v37's diagnostics showed the header ("Playing X of Y") updating
# immediately and correctly. Root cause: v36's push only replayed the
# subscribed request through _on_player_event, which delivers a generic
# response - apparently enough for scalar header fields, but not what
# the list widget listens for. Found by reading cli.py's other real
# subscription-push example (PLAYER_PRESETS_UPDATED) side by side: it's
# the only place in aioslimproto that pushes a live list update, and it
# does so with a specific [player_id, item_loop, "replace", player_id]
# array queued directly onto the client, not a replayed-request
# response. Added a second push that copies that exact shape, using our
# own real item_loop (now factored into a shared
# _build_queue_item_loop() helper, used by both _handle_queue_status and
# this new push, rather than duplicated). Offset/limit for the rebuild
# are read from the subscribed request's own stored args, not
# hardcoded. NOT yet verified against a real device test - this second
# push's own channel-key/payload-shape assumptions are copied from the
# one other real example in this codebase, not confirmed live yet.
# (v37 was: Diagnostic-only change, no behavior
# fix yet - a real device test after v36 reported the queue reading 0
# right after a device reboot, and still 0 after adding a track. The
# real captures from that test actually show real_total=16 then 17
# correctly on the menu='menu' (dedicated queue-view) path throughout -
# nothing wrong found there. But the plain polling status path (the
# routine heartbeat driving the compact Now Playing header, separate
# from the dedicated queue-view screen) was completely silent - no
# print at all, either on its cheap-patch success or on a
# get_active_queue() miss - so there was no way to confirm or rule out
# whether the reported "0" came from that path instead. Added prints to
# both branches (queue is None; and the plain-status cheap-patch
# result) so the next capture can show directly which path, if either,
# is actually returning 0.
# (v36 was: Added a real, event-driven push to
# the queue-view screen after playlistcontrol add/insert, rather than
# leaving updates to wait out aioslimproto's own periodic subscription
# replay. Read that periodic loop's real source first (cli.py's
# _do_periodic): it already re-sends every client's stored subscription
# every 60 seconds flat, ignoring whatever subscribe:N interval the
# device actually asked for - explains the ~60s update lag reported on
# a real device test. Considered shrinking that hardcoded interval
# instead (a one-line patch) but rejected it: refreshing every
# subscription on a fixed short timer regardless of whether anything
# changed is real, avoidable load on a low-power device, for every
# client, all the time - not worth it next to a real push that only
# fires on an actual change. Went with reusing aioslimproto's existing
# _on_player_event instead (cli.py) - already does real event-driven
# push for PLAYER_CONNECTED/PLAYER_UPDATED and
# PLAYER_PRESETS_UPDATED, just never for a queue change specifically.
# _handle_playlistcontrol now synthesizes a PLAYER_UPDATED SlimEvent
# after a successful add/insert (not load, which already triggers a
# real one through normal playback) - confirmed via a real trace
# through both event systems that _on_player_event is private to
# SlimProtoCLI's own CometD bookkeeping, entirely separate from
# SlimServer.subscribe()'s event bus (what provider.py listens on), so
# this has no other side effects beyond the intended push. One thing
# NOT confirmed against a real device test yet - see
# _handle_playlistcontrol's own inline comment: whether the queue-view
# subscription's real response-channel key actually matches the one
# _on_player_event's PLAYER_UPDATED branch checks.
# (v35 was: Fixed the real cause of newly-added
# queue tracks "not showing" for 3-4 minutes at a time - not a caching or
# propagation delay as first suspected, but every request touching those
# tracks failing outright. Confirmed via a real traceback:
# mass.player_queues.player_media_from_queue_item() (used by
# _handle_queue_status since v29) raises
# InvalidDataError("Queue session_id is None") for any queue item that
# isn't already part of an active streaming session - which in practice
# is only ever current_media/next_media, the exact same 2-item universe
# the original built-in _handle_status was stuck in, just reached a
# different way. A newly-added track only got a session (and stopped
# raising) once playback advanced far enough for MA's own preload
# behavior to reach it - which is what looked like a multi-minute delay,
# but was actually every intervening request for that item failing
# silently (caught by __call__, logged, re-raised) rather than returning
# stale-but-present data. Real fix: stopped calling
# player_media_from_queue_item() for the listing entirely - it was never
# the right tool for a read-only "what's in the queue" display, only for
# something about to actually stream. Rows are now built directly from
# QueueItem's own fields (.name, .duration, .media_item for
# artist/album/uri via the underlying Track) - confirmed via real
# signature introspection before using any of them, not guessed - plus
# mass.metadata.get_image_url_for_item(), the same real helper already
# used elsewhere in this file for icon serving, reused rather than
# reinvented.
# (v34 was: Fixed "Now Playing 1 of 2" no
# matter how large the real queue was - a real device report, separate
# from the dedicated queue-view screen (which v32/v33 already handled
# correctly). Root cause: the built-in _handle_status's plain polling
# variant (no menu='menu', fires every few seconds) builds its own
# "playlist_tracks"/"playlist_cur_index" fields from the same
# current_media/next_media 2-item cap - _handle_queue_status was only
# intercepting the menu='menu' variant, so this one still hit the
# untouched built-in every time. Broadened _dispatch to route every
# status call through _handle_queue_status, and split the handler: a
# cheap patch (playlist_tracks/playlist_cur_index from
# get_active_queue(), already sync/lightweight) applies to every status
# call; the expensive per-item queue fetch/conversion (items(),
# player_media_from_queue_item(), menu_item_from_media_details() for
# each row) still only runs for the menu='menu' case, to avoid doing
# that work on every few-second heartbeat poll.
# (v33 was: Diagnostic-only change, no behavior
# fix yet - a real device test after v32 showed the last 2 added tracks
# "not showing" in the queue view, but walking the actual real_total/
# returned counts across that same capture's full add sequence checked
# out correctly by index math every step of the way (offset+limit did
# cover the expected index for the newest items, assuming append-order
# indexing). Counts alone can't confirm WHICH tracks actually came back
# in item_loop, only that some N items did - added real item titles
# (queue_item.name) to the [BL] queue status print so the next capture
# can confirm directly whether the missing tracks are actually absent
# from the response, rather than inferring from index arithmetic again.
# (v32 was: Fixed a real design flaw in
# _handle_queue_status, not another typo - v29-v31 all built the
# returned status response entirely from scratch (a bare {"count",
# "offset", "item_loop"} dict), which stopped crashing as of v31 but
# still left the Now Playing screen broken on a real device test (song
# plays, screen doesn't render). The real built-in _handle_status
# computes much more than item_loop - player_name, mode, power, alarm
# data, base.actions.more (this screen's own "more" button), preset_loop/
# preset_data - all silently missing from a from-scratch dict. Fixed by
# calling the real built-in first (self.provider.slimproto.cli.
# _handle_status - the same real CLI instance provider.py already
# reaches into directly for _handle_jsonrpc_client) with the exact
# args/kwargs split this file's own real log captures already confirmed
# aioslimproto's dispatcher uses, then only overwriting
# item_loop/count/offset with real queue data afterward - everything
# else the built-in gets right now stays right.
# (v31 was: Fixed v30's remaining half of the
# same mistake - removed await from all three player_queues calls
# uniformly on the theory they'd share async-ness since they're the same
# controller's same accessor shape. Wrong: get_active_queue()/.items()
# are genuinely sync (confirmed, v30 fixed those correctly), but
# .player_media_from_queue_item() IS async - confirmed via a real
# traceback this time ('coroutine' object has no attribute 'uri', once
# player_media was used before being awaited). Restored await on that
# one call only. Same-controller/same-shape is not a reliable signal for
# async-ness in this codebase - each method needs its own real
# confirmation, not an inference from its neighbors.
# (v30 was: Fixed v29's _handle_queue_status -
# broke the Now Playing screen entirely (not a regression in trackinfo
# or playlistcontrol, both confirmed still working via the same real log
# capture that caught this) by awaiting player_queues.get_active_queue(),
# which isn't a coroutine. Confirmed via a real traceback (TypeError:
# 'PlayerQueue' object can't be awaited), not guessed - the earlier
# signature introspection showed real parameter/return types but never
# actually confirmed async-ness one way or the other. Removed await from
# all three player_queues calls this function makes
# (get_active_queue/.items/.player_media_from_queue_item), not just the
# one that errored first, since all three are the same controller's
# same shape of lightweight accessor.
# (v29 was: Queue/Now-Playing screen only ever
# showed 2 tracks (current+next), sliding forward as each finished,
# regardless of how many were actually queued - confirmed via a real
# docker log capture that this screen sends a distinct request
# (command='status' args=['-', 10] kwargs={'menu': 'menu', ...}, unlike
# routine polling status calls, which never carry 'menu') that was
# falling through to aioslimproto's built-in _handle_status. Read that
# source directly: it only ever reports player.current_media/next_media
# - two fixed slots, no real queue concept, offset/limit accepted but
# never used to slice anything - so the response was always exactly 2
# items no matter what. Added _handle_queue_status, intercepting only
# the menu='menu' variant (routine status polling is untouched), sourcing
# the real queue via mass.player_queues.get_active_queue()/.items()/
# .player_media_from_queue_item() (real methods, confirmed via direct
# signature introspection in the running container, not guessed) and
# building rows via aioslimproto's own menu_item_from_media_details(...,
# include_actions=True) - reused, not reinvented. Two things not yet
# verified against a real device test - see _handle_queue_status's own
# docstring: offset='-' semantics, and async-ness of the two
# player_queues methods just added.
# (v28 was: Long-press on a track (the "more"
# action -> trackinfo/items command every *_base_actions() already
# declares) previously got no response at all - a blank screen, not an
# error - because _dispatch only ever handled menu/playlistcontrol/
# browselibrary; anything else, trackinfo included, fell straight
# through to NotImplementedError. Confirmed via a real docker log
# capture (command='trackinfo' args=['items', 0, 200]
# kwargs={..., 'track_id': 18, ...} -> NotImplementedError every time)
# before writing anything, not guessed at from the code alone. Added
# _handle_trackinfo, wired into _dispatch, returning a 3-row context
# menu (Play Song/Add to End of Queue/Play Next) built by analogy to
# tracks_base_actions' own play/add/add-hold entries. NOT verified
# against a real LMS trackinfo capture - same caveat
# playlists_base_actions already flags for itself - real LMS's actual
# trackinfo menu also has non-playback rows (credits, genre, etc.) this
# doesn't attempt. albuminfo/playlistinfo/artistinfo (the "more" action
# on album/playlist/artist rows) are the same shape of gap, still
# unhandled - not built yet, only trackinfo was.)
# (v27 was: playlistcontrol now handles cmd=add/insert, not just
# cmd=load - the "Add"/"Play Next" context-menu actions were already
# declared in tracks_base_actions (and albums/playlists' own
# base_actions) and already reaching this handler -
# _handle_playlistcontrol was just rejecting them outright with
# NotImplementedError regardless of cmd. Added a cmd -> QueueOption
# mapping (add -> QueueOption.ADD, insert -> QueueOption.NEXT) used by
# both the uri and track_id branches; load's existing, already-verified
# PLAY behavior is unchanged.)
# (v26 was: Fixed Search All playback for real this time - v25's fix
# made rows fire an action, but the outgoing playlistcontrol request
# never included the item's own track_id/uri (confirmed via a real
# docker log capture: only the shared/static params - cmd, sort,
# search, menu - made it through). Root cause: itemsParams (the
# mechanism meant to inject an item's own field into the outgoing
# command) is documented as a base-level mechanism specifically
# (SlimBrowse Protocol reference: "In base level commands, this defines
# the name of the field in the item...") - duplicating the same
# template onto each item's own "actions" (v25's fix, needed for a
# different reason - see below) didn't get the same resolution. Fixed
# with a new _standalone_actions() helper: bakes each item's own field
# data (commonParams/addallParams/presetParams, whichever a given
# action actually references) directly into that action's params,
# dropping itemsParams entirely - nothing left to resolve indirectly.
# General, not track-specific - covers every action in all four
# templates.)
# (v25 was: fixed rows doing nothing at all when selected from Search
# All - every type's rows rely on "base": {"actions": ...} at the
# response level for their "goAction" shorthand to resolve against, and
# the combined response had no base at all, since no single template
# fits all seven mixed types. Fixed by attaching each row's own matching
# actions block directly, per item.)
# (v24 was: "Search All" added as the first option in the search
# category menu - real use showed picking a type every time got in the
# way of the common case. Queries all seven types in parallel, reusing
# each type's own get_X() function entirely, then paginates the
# combined list locally. v23 was: search input field fix - "input": 3
# became "input": {"len": 3} (the structured schema form) - fixed a
# "wiggle, nothing happens" rejection on selecting Search. v22 was: real
# search - a new "Search" home menu tile + category menu, each type's
# get_X() function gaining a search= param threaded into
# library_items().)
# (v1-v21 summary: real MA data for artists/albums/tracks/playlists/
# radio/podcasts/audiobooks, each following the same MediaControllerBase
# pattern; real cover/photo/logo art via mass.metadata with a priority
# picker - internet first, then local standalone file, then
# embedded-in-audio-file last; single-track/station/episode playback via
# playlistcontrol, preferring a "uri" tag for anything not guaranteed to
# playlistcontrol, preferring a "uri" tag for anything not guaranteed to
# be an MA library item; real chrome icons from static/, 1:1 filename
# matching, solid-color placeholder as the last-resort fallback
# throughout; menu taxonomy matches MA's own root UI order, not real
# LMS's; consistent items/item variable naming across the flat-listing
# and sub-item functions, prep for an eventual shared helper without
# merging yet.)
# If something that used to work now looks like a placeholder/solid
# color, or a whole section is empty, grep the deployed file for this
# exact line first - if it's an older vN or missing, the new file never
# landed (reinject.sh needs re-running, or the container restart didn't
# pick it up) - that's the most common cause by far.

import asyncio
import inspect
import logging
import re
import struct
import urllib.parse
import zlib
from contextlib import suppress
from datetime import datetime, timedelta, timezone
from pathlib import Path

from aiohttp import web
# MediaDetails/menu_item_from_media_details: reused directly from
# aioslimproto rather than reinvented - menu_item_from_media_details()
# (cli.py) is the same row-builder real LMS-shaped queue/menu rows
# already use elsewhere (confirmed by reading its real source before
# using it, not guessed at), and MediaDetails (models.py) is just the
# .url/.metadata shape it expects as input - the same shape player.py's
# own _handle_play_url_for_slimplayer already builds for
# current_media/next_media.
from aioslimproto.cli import menu_item_from_media_details
from aioslimproto.models import EventType, MediaDetails, SlimEvent
from music_assistant_models.enums import ImageType, PlaybackState, QueueOption
from music_assistant_models.errors import MediaNotFoundError

logger = logging.getLogger("music_assistant.squeezelite.browselibrary")

# icon_id bare-path fallback (handle_unmatched below) - see that function's
# own comment for the full "why" (JiveLite requesting a bare, unwrapped
# icon_id path instead of the proper '/music/{icon_id}/cover_{size}'). Now
# that mass is threaded through via make_icon_routes() below, this fallback
# also attempts a real art lookup before falling back to the placeholder -
# see _fetch_real_item_art and the "Icon / cover art serving" section
# further down for the full real-art implementation.

# Keys real LMS echoes through from the browse request into every base.action's
# params at every subsequent drill-down level - verified against a real capture.
CONTEXT_KEYS = ("artist_id", "role_id", "menu_roles", "menu_mode", "menu", "album_id",
                "playlist_id", "podcast_id", "search")



def _context(kwargs):
    # aioslimproto's own parse_args coerces numeric-looking tag values to
    # int/float (e.g. "artist_id:3" -> 3, not "3") - normalize back to str
    # for consistency with everything else here, which treats ids as strings.
    return {k: str(kwargs[k]) for k in CONTEXT_KEYS if k in kwargs}


def _paginate(items, index, quantity):
    index = index or 0
    total = len(items)
    window = items[index:index + quantity] if quantity is not None else items[index:]
    return window, total, index


ARTIST_LIST_BASE_ACTIONS = {
    "go": {
        "player": 0,
        "params": {"menu": 1, "role_id": "ALBUMARTIST", "menu_mode": "artists", "menu_roles": "ALBUMARTIST", "mode": "albums"},
        "cmd": ["browselibrary", "items"],
        "itemsParams": "commonParams",
    },
    "add": {
        "player": 0,
        "params": {"menu_roles": "ALBUMARTIST", "role_id": "ALBUMARTIST", "menu_mode": "artists", "cmd": "add", "menu": 1},
        "cmd": ["playlistcontrol"],
        "itemsParams": "commonParams",
    },
    "play": {
        "player": 0,
        "nextWindow": "nowPlaying",
        "params": {"menu_roles": "ALBUMARTIST", "role_id": "ALBUMARTIST", "cmd": "load", "menu_mode": "artists", "menu": 1},
        "itemsParams": "commonParams",
        "cmd": ["playlistcontrol"],
    },
    "add-hold": {
        "player": 0,
        "itemsParams": "commonParams",
        "cmd": ["playlistcontrol"],
        "params": {"menu_roles": "ALBUMARTIST", "menu": 1, "menu_mode": "artists", "cmd": "insert", "role_id": "ALBUMARTIST"},
    },
    "more": {
        "player": 0,
        "params": {"menu": 1},
        "itemsParams": "commonParams",
        "cmd": ["artistinfo", "items"],
        "window": {"isContextMenu": 1},
    },
    **{
        f"set-preset-{n}": {
            "player": 0,
            "itemsParams": "presetParams",
            "cmd": ["jivefavorites", "set_preset", f"key:{n}"],
        }
        for n in range(10)
    },
}


def albums_base_actions(kwargs):
    """Sparse on purpose for the no-artist_id case - confirmed directly
    against a real LMS response that role_id/menu_roles are genuinely
    absent there, not something to "correct"."""
    ctx = _context(kwargs)
    actions = {
        "go": {"player": 0, "cmd": ["browselibrary", "items"], "itemsParams": "commonParams",
               "params": {**ctx, "mode": "tracks"}},
        "more": {"player": 0, "cmd": ["albuminfo", "items"], "itemsParams": "commonParams",
                 "window": {"isContextMenu": 1}, "params": dict(ctx)},
        "play": {"player": 0, "cmd": ["playlistcontrol"], "itemsParams": "commonParams",
                 "nextWindow": "nowPlaying", "params": {**ctx, "cmd": "load"}},
        "add": {"player": 0, "cmd": ["playlistcontrol"], "itemsParams": "commonParams",
                "params": {**ctx, "cmd": "add"}},
        "add-hold": {"player": 0, "cmd": ["playlistcontrol"], "itemsParams": "commonParams",
                     "params": {"menu": ctx.get("menu", 1), "cmd": "insert"}},
    }
    for n in range(10):
        actions[f"set-preset-{n}"] = {"player": 0, "itemsParams": "presetParams",
                                       "cmd": ["jivefavorites", "set_preset", f"key:{n}"]}
    return actions


def playlists_base_actions(kwargs):
    """base.actions for a playlists item_loop - built by analogy to
    albums_base_actions above (same shape: "go" drills into "mode":
    "tracks" with playlist_id in context; play/add/add-hold hit
    playlistcontrol the same way), NOT verified against a real LMS
    capture the way almost everything else in this file is - playlists
    were never implemented in the standalone scaffold either, so there's
    no prior real-capture data point to check this against. If real LMS
    turns out to send something different here once tested, this is the
    function to fix.
    """
    ctx = _context(kwargs)
    actions = {
        "go": {"player": 0, "cmd": ["browselibrary", "items"], "itemsParams": "commonParams",
               "params": {**ctx, "mode": "tracks"}},
        "more": {"player": 0, "cmd": ["playlistinfo", "items"], "itemsParams": "commonParams",
                 "window": {"isContextMenu": 1}, "params": dict(ctx)},
        "play": {"player": 0, "cmd": ["playlistcontrol"], "itemsParams": "commonParams",
                 "nextWindow": "nowPlaying", "params": {**ctx, "cmd": "load"}},
        "add": {"player": 0, "cmd": ["playlistcontrol"], "itemsParams": "commonParams",
                "params": {**ctx, "cmd": "add"}},
        "add-hold": {"player": 0, "cmd": ["playlistcontrol"], "itemsParams": "commonParams",
                     "params": {"menu": ctx.get("menu", 1), "cmd": "insert"}},
    }
    for n in range(10):
        actions[f"set-preset-{n}"] = {"player": 0, "itemsParams": "presetParams",
                                       "cmd": ["jivefavorites", "set_preset", f"key:{n}"]}
    return actions


def tracks_base_actions(kwargs):
    ctx = _context(kwargs)
    common = {**ctx, "sort": "albumtrack"}
    actions = {
        # itemsParams: "commonParams" (was "playallParams") - our own
        # playlistcontrol handler (BrowseLibraryHandler._handle_playlistcontrol)
        # only understands "play this one track_id now" for now, not real
        # LMS's "load the whole album, starting at play_index" semantics -
        # commonParams is where the item's track_id lives (see get_tracks
        # below); play_index/playallParams is left in place on the item for
        # if/when the fuller album-context "play from here" behavior is
        # wired up, but nothing consumes it yet.
        "go": {"player": 0, "cmd": ["playlistcontrol"], "itemsParams": "commonParams",
               "nextWindow": "nowPlaying", "params": {**common, "cmd": "load"}},
        "play": {"player": 0, "cmd": ["playlistcontrol"], "itemsParams": "commonParams",
                 "nextWindow": "nowPlaying", "params": {**common, "cmd": "load"}},
        "add": {"player": 0, "cmd": ["playlistcontrol"], "itemsParams": "addallParams",
                "params": {**common, "cmd": "add"}},
        "add-hold": {"player": 0, "cmd": ["playlistcontrol"], "itemsParams": "commonParams",
                     "params": {"menu": ctx.get("menu", 1), "cmd": "insert"}},
        "more": {"player": 0, "cmd": ["trackinfo", "items"], "itemsParams": "commonParams",
                 "window": {"isContextMenu": 1}, "params": dict(ctx)},
    }
    for n in range(10):
        actions[f"set-preset-{n}"] = {"player": 0, "itemsParams": "presetParams",
                                       "cmd": ["jivefavorites", "set_preset", f"key:{n}"]}
    return actions


async def get_artists(mass, index=0, quantity=None, search=None, favorite_only=False):
    """Real MA data: mass.music.artists.library_items()/.library_count().

    favorite_only (v45): threads straight into library_items(favorite=...)/
    library_count(favorite_only=...) - both real, already-existing params
    on the base controller (confirmed via the real music-assistant/server
    source, controllers/music/media/base.py) - not a new filter invented
    here, just wiring an existing one through. Used by the new Favorites
    browse tree (see get_favorites_all()/MY_MUSIC_NODE below) exactly the
    same way search= already threads through every one of these functions.

    summary=False (full Artist objects, not the slimmer summary dataclass) -
    verified field names (item_id, name) come from the full Artist/MediaItem
    dataclass in music_assistant_models; the summary dataclass wasn't pulled
    and checked, so this trades a bit of query weight for certainty rather
    than guessing its shape.

    Real per-artist photos: icon_id is "artist-<item_id>" (e.g.
    "artist-7"), not the bare item_id albums use ("42") - albums and
    artists each have their own independent id space in MA, so an
    unqualified numeric icon_id would be ambiguous (could collide with an
    unrelated album's id) once both types share the same
    /music/{icon_id}/cover route. See _fetch_real_item_art below for the
    matching lookup side of this.
    """
    limit = quantity if quantity is not None else 500
    items = await mass.music.artists.library_items(
        limit=limit, offset=index, search=search, summary=False,
        favorite=True if favorite_only else None)
    if search:
        # library_count() does NOT accept a search= param (checked the real
        # base class signature - only favorite_only) - can't get an exact
        # match count cheaply, so this reports what we actually fetched
        # instead of the full library size. Real limitation: a match set
        # bigger than `limit` undercounts here - accepted as reasonable for
        # a search result set, which is normally small; not silently wrong,
        # just imprecise past the fetch limit.
        total = len(items)
    else:
        total = await mass.music.artists.library_count(favorite_only=favorite_only)
    item_loop = [
        {
            "text": item.name, "type": "playlist",
            "commonParams": {"artist_id": str(item.item_id)},
            "textkey": item.name[0].upper() if item.name else "",
            "icon": f"music/artist-{item.item_id}/cover", "icon-id": f"artist-{item.item_id}",
            "presetParams": {
                "favorites_type": "playlist", "favorites_title": item.name,
                "favorites_url": f"db:contributor.name={item.name.replace(' ', '%20')}",
                "icon": f"music/artist-{item.item_id}/cover",
            },
        }
        for item in items
    ]
    return {
        "count": total, "offset": index,
        "window": {"windowStyle": "home_menu"},
        "base": {"actions": ARTIST_LIST_BASE_ACTIONS},
        "item_loop": item_loop,
    }


async def get_all_tracks(mass, kwargs, index=0, quantity=None, search=None, favorite_only=False):
    """Real MA data: TracksController.library_items()/.library_count() -
    library_items() IS overridden here (same as Artists/Podcasts), adding
    favorite/search/genre/provider/explicit-content filtering, but with
    the same limit/offset/summary defaults and semantics for the plain,
    unfiltered call we make here - checked directly, not assumed.
    library_count() is NOT overridden - the plain inherited version, same
    as always.

    Flat, root-level track browse - matches Music Assistant's own web UI
    taxonomy (Artists/Albums/Tracks/Playlists/...), added alongside the
    Artists/Tracks menu restructure (collapsing "Album Artists"/"All
    Artists" into one "Artists" tile, since get_artists() above already
    doesn't distinguish the two - see its own docstring). Uses track_id
    (a safe library-item lookup), same as get_tracks() above - these
    genuinely are library tracks fetched via the standard controller, not
    provider-native items the way playlist tracks/podcast episodes are.

    icon_list (cover art) rather than a plain text list - a flat,
    unfiltered list of every track title alone was genuinely hard to
    navigate/scan. Reuses the album's own bare-numeric icon_id scheme
    (the same one get_albums() uses) rather than inventing a new
    "track-" prefix: Track.album: Album | ItemMapping | None is a real
    field, and Track.image's own logic already prefers self.album.image
    when present, confirming the album is the canonical art source for a
    track, not the track itself - .item_id is present on both a full
    Album and a lightweight ItemMapping, so this works either way with
    zero new code needed in _fetch_real_item_art. Falls through to the
    solid-color placeholder (same as everywhere else) when a track
    genuinely has no album (singles/compilation entries without album
    metadata) - not treated as an error.

    Row text is "Title\\nArtist" (two lines) using Track.artist_str - a
    real property, confirmed directly from source: same "/".join(x.name
    for x in self.artists) pattern as Album.artist_str, already verified
    and in use in get_albums() above. The \\n convention itself matches
    real LMS's own documented protocol ("text may contain \\n, in which
    case the item is displayed on multiple lines" - SlimBrowse Protocol
    reference) - not a windowStyle change, just the same officially
    documented two-line text convention Albums already uses. Falls back
    to the bare title if a track genuinely has no artists attached.
    """
    limit = quantity if quantity is not None else 500
    items = await mass.music.tracks.library_items(
        limit=limit, offset=index, search=search, summary=False,
        favorite=True if favorite_only else None)
    if search:
        total = len(items)  # library_count() has no search= param - see get_artists' docstring
    else:
        total = await mass.music.tracks.library_count(favorite_only=favorite_only)
    item_loop = []
    for i, item in enumerate(items):
        text = item.name
        if item.artist_str:
            text = f"{item.name}\n{item.artist_str}"
        row = {
            "text": text, "type": "audio", "style": "itemplay", "goAction": "play",
            "commonParams": {"track_id": str(item.item_id)},
            "playallParams": {"play_index": index + i},
            "presetParams": {"favorites_type": "audio", "favorites_title": item.name,
                              "favorites_url": item.uri},
        }
        if item.album is not None:
            row["icon"] = f"music/{item.album.item_id}/cover"
            row["icon-id"] = str(item.album.item_id)
        item_loop.append(row)
    return {
        "count": total, "offset": index, "window": {"windowStyle": "icon_list"},
        "base": {"actions": tracks_base_actions(kwargs)},
        "item_loop": item_loop,
    }


async def get_albums(mass, artist_id, kwargs, index=0, quantity=None, search=None, favorite_only=False):
    """Real MA data.

    Two different MA APIs depending on whether we're filtering by artist:
    - artist_id given: ArtistsController.albums(artist_id, "library") - a
      relationship query verified to return the full (unpaginated) list of
      an artist's library albums, so we paginate it ourselves below exactly
      like Stage 1 paginated its in-memory list.
    - no artist_id (all albums): AlbumsController.library_items()/
      .library_count(), which DO paginate/count server-side.

    The no-artist_id ("All Albums") case also gets three things the
    artist-filtered case doesn't - ported from the standalone scaffold,
    which verified all three against a real LMS capture of exactly this
    view: a flat, mixed-artist list needs to show whose album is whose, so
    it can't just reuse the filtered case's plain title-only text.
      - two-line "text": "Album Title\\nArtist Name"
      - "textkey" (first letter of the title) - powers the device's
        alphabet jump-scroll bar, which the filtered case doesn't show
      - a "presetParams" block, so albums are favoritable from this view
    Unlike the scaffold (which had to look artist names up in a separate
    id->name dict, since its hardcoded data model didn't attach artist
    objects to each album), our real Album objects already carry their
    artists directly - album.artist_str (MediaItem's own property,
    "/".join of each artist's name) needs no extra lookup.
    """
    if artist_id is not None:
        albums = await mass.music.artists.albums(artist_id, "library")
        window, total, offset = _paginate(albums, index, quantity)
    else:
        limit = quantity if quantity is not None else 500
        window = await mass.music.albums.library_items(
            limit=limit, offset=index, search=search, summary=False,
            favorite=True if favorite_only else None)
        if search:
            total = len(window)  # library_count() has no search= param - see get_artists' docstring
        else:
            total = await mass.music.albums.library_count(favorite_only=favorite_only)
        offset = index
    item_loop = []
    for album in window:
        item = {
            "text": album.name, "type": "playlist",
            "commonParams": {"album_id": str(album.item_id), "performance": ""},
            "icon": f"music/{album.item_id}/cover", "icon-id": str(album.item_id),
        }
        if artist_id is None:
            artist_str = album.artist_str
            if artist_str:
                item["text"] = f"{album.name}\n{artist_str}"
            item["textkey"] = album.name[0].upper() if album.name else ""
            favorites_url = f"db:album.title={urllib.parse.quote(album.name)}"
            if artist_str:
                favorites_url += f"&contributor.name={urllib.parse.quote(artist_str)}"
            item["presetParams"] = {
                "icon": item["icon"],
                "favorites_title": album.name,
                "favorites_url": favorites_url,
                "favorites_type": "playlist",
            }
        item_loop.append(item)
    return {
        "count": total, "offset": offset,
        "window": {"windowStyle": "home_menu" if artist_id else "icon_list"},
        "base": {"actions": albums_base_actions(kwargs)},
        "item_loop": item_loop,
    }


async def get_tracks(mass, album_id, kwargs, index=0, quantity=None):
    """Real MA data: AlbumsController.tracks(album_id, "library").

    in_library_only=False (v48; was True) - confirmed via the real
    music-assistant/server source (controllers/music/media/albums.py) this
    was the actual cause of a real report: some albums showed fine in
    browse (real art/title, since the ALBUM has its own local library
    row) but selecting them showed no tracks at all. in_library_only=True
    returns ONLY tracks that separately have their own local library
    row (get_library_album_tracks) - favoriting/adding an ALBUM does not
    guarantee every individual TRACK also got its own local row, so an
    album that only got that partial treatment returned an empty or
    partial list under the old True. False keeps every in-library track
    but ALSO reaches out live to the actual provider (Spotify, etc.) and
    merges in whatever tracks aren't mirrored locally yet - the real
    fallback this needed. Real tradeoff worth knowing: this means an
    affected album's response now includes a live provider round-trip
    instead of being purely local-DB, so it can be measurably slower for
    exactly the albums this fixes - accepted, since no tracks at all is
    worse than a slower response.

    Still sorted by (disc_number, track_number) either way (confirmed in
    the same source), still unpaginated (same as .albums() above), so
    this still paginates the returned list itself.

    favorites_url now uses the track's own `.uri` (e.g. "library://track/42"),
    auto-generated by the MediaItem dataclass itself from (media_type,
    provider, item_id) - a real, MA-native identifier, unlike the artist/
    album favorites_url below which are still LMS's own "db:" convention
    (see this module's docstring for why those are flagged, not fixed, here).
    """
    items = await mass.music.albums.tracks(album_id, "library", in_library_only=False) \
        if album_id is not None else []
    window, total, offset = _paginate(items, index, quantity)
    item_loop = [
        {
            "text": item.name, "type": "audio", "style": "itemplay", "goAction": "play",
            "commonParams": {"track_id": str(item.item_id)},
            "playallParams": {"play_index": offset + i},
            "presetParams": {"favorites_type": "audio", "favorites_title": item.name,
                              "favorites_url": item.uri},
        }
        for i, item in enumerate(window)
    ]
    return {
        "count": total, "offset": offset, "window": {"windowStyle": "text_list"},
        "base": {"actions": tracks_base_actions(kwargs)},
        "item_loop": item_loop,
    }


async def get_playlists(mass, kwargs, index=0, quantity=None, search=None, favorite_only=False):
    """Real MA data: PlaylistController.library_items()/.library_count() -
    same generic MediaControllerBase pattern as get_artists/get_albums'
    no-artist_id branch above, verified the same way (no override of
    either method in playlists.py - uses the base class implementation
    as-is). Playlists don't nest under anything else (unlike albums,
    which can be filtered by artist), so this is always the flat "All
    Playlists" list - no artist_id-style branching needed here.

    Real per-playlist art: icon_id is "playlist-<item_id>", same
    namespacing pattern as "artist-<item_id>" - see _fetch_real_item_art
    below, which now handles all three prefixes.
    """
    limit = quantity if quantity is not None else 500
    items = await mass.music.playlists.library_items(
        limit=limit, offset=index, search=search, summary=False,
        favorite=True if favorite_only else None)
    if search:
        total = len(items)  # library_count() has no search= param - see get_artists' docstring
    else:
        total = await mass.music.playlists.library_count(favorite_only=favorite_only)
    item_loop = [
        {
            "text": item.name, "type": "playlist",
            "commonParams": {"playlist_id": str(item.item_id)},
            "textkey": item.name[0].upper() if item.name else "",
            "icon": f"music/playlist-{item.item_id}/cover", "icon-id": f"playlist-{item.item_id}",
            "presetParams": {
                "favorites_type": "playlist", "favorites_title": item.name,
                "favorites_url": item.uri,
                "icon": f"music/playlist-{item.item_id}/cover",
            },
        }
        for item in items
    ]
    return {
        # "icon_list" by analogy to the unfiltered Albums view (a flat,
        # cover-art-driven list) - not verified against a real LMS capture
        # of Playlists specifically, same caveat as playlists_base_actions.
        "count": total, "offset": index,
        "window": {"windowStyle": "icon_list"},
        "base": {"actions": playlists_base_actions(kwargs)},
        "item_loop": item_loop,
    }


async def get_playlist_tracks(mass, playlist_id, kwargs, index=0, quantity=None):
    """Real MA data: PlaylistController.tracks(playlist_id, "library") -
    genuinely different from AlbumsController.tracks() in two ways that
    matter here, confirmed by reading the real source rather than assumed
    from the naming similarity:

      1. It's an async generator, not a plain awaitable list - "playlist
         tracks are not stored in the db, we always fetch them (cached)
         from the provider" (that method's own docstring). We consume it
         fully into a list before paginating, same end result as
         get_tracks() above, just gathered differently. The 2000-item cap
         below is defensive only - the method's own docstring says a
         dynamic/endless playlist's provider returns a bounded sample and
         terminates on its own (an empty page breaks the loop), so this
         should never actually bite; it exists in case a future provider
         doesn't honor that.
      2. Yielded items (type PlaylistPlayableItem) are NOT guaranteed to
         be MA library items the way an album's tracks are - they may be
         provider-native objects (e.g. a Spotify-domain item_id) fetched
         fresh from whichever provider backs the playlist. That means
         commonParams here uses the track's own "uri" (a MediaItem
         property every one of these still has, regardless of source),
         not "track_id" - our own _handle_playlistcontrol prefers a "uri"
         tag over a track_id lookup for exactly this reason. Contrast
         with get_tracks() above, which safely uses track_id because
         album tracks genuinely are library items.
    """
    items = []
    if playlist_id is not None:
        async for item in mass.music.playlists.tracks(playlist_id, "library"):
            items.append(item)
            if len(items) >= 2000:  # defensive cap - see docstring above
                break
    window, total, offset = _paginate(items, index, quantity)
    item_loop = [
        {
            "text": item.name, "type": "audio", "style": "itemplay", "goAction": "play",
            "commonParams": {"uri": item.uri},
            "playallParams": {"play_index": offset + i},
            "presetParams": {"favorites_type": "audio", "favorites_title": item.name,
                              "favorites_url": item.uri},
        }
        for i, item in enumerate(window)
    ]
    return {
        "count": total, "offset": offset, "window": {"windowStyle": "text_list"},
        "base": {"actions": tracks_base_actions(kwargs)},
        "item_loop": item_loop,
    }


async def get_radio_stations(mass, kwargs, index=0, quantity=None, search=None, favorite_only=False):
    """Real MA data: RadioController.library_items()/.library_count() - same
    generic MediaControllerBase pattern as get_playlists above, verified the
    same way (no override of either method in radio.py - uses the base
    class implementation as-is).

    Unlike albums/playlists, a radio row plays directly rather than
    drilling into a sub-list: RadioController.radio_tracks() exists, but
    it's specifically for *dynamic* stations (radio.is_dynamic) -
    algorithmically-generated stations that return a fresh batch of real
    Track objects on demand. A plain internet radio stream isn't dynamic
    at all - it's one continuous stream, nothing to browse into. So each
    row here is shaped like a track row (tracks_base_actions, "go"/"play"
    sending a "uri" tag), reusing the uri-based playback path already
    built for playlist tracks above rather than adding anything new -
    _handle_playlistcontrol already prefers "uri" over "track_id", and
    Radio gets the same auto-generated .uri every MediaItem does.

    Real per-station art: icon_id is "radio-<item_id>", same namespacing
    pattern as "artist-<id>"/"playlist-<id>" - see _fetch_real_item_art.

    windowStyle "icon_list" is a judgment call, not verified against a
    real LMS capture (no prior data point for Radio anywhere in this
    project, same caveat as playlists_base_actions) - station logos seem
    like a natural fit for a cover-art-style grid, matching Albums/
    Playlists rather than a plain text list, but flag if real LMS turns
    out to want something different once tested.
    """
    limit = quantity if quantity is not None else 500
    items = await mass.music.radio.library_items(
        limit=limit, offset=index, search=search, summary=False,
        favorite=True if favorite_only else None)
    if search:
        total = len(items)  # library_count() has no search= param - see get_artists' docstring
    else:
        total = await mass.music.radio.library_count(favorite_only=favorite_only)
    item_loop = [
        {
            "text": item.name, "type": "audio", "style": "itemplay", "goAction": "play",
            "commonParams": {"uri": item.uri},
            "icon": f"music/radio-{item.item_id}/cover", "icon-id": f"radio-{item.item_id}",
            "presetParams": {"favorites_type": "audio", "favorites_title": item.name,
                              "favorites_url": item.uri},
        }
        for item in items
    ]
    return {
        "count": total, "offset": index, "window": {"windowStyle": "icon_list"},
        "base": {"actions": tracks_base_actions(kwargs)},
        "item_loop": item_loop,
    }


async def get_audiobooks(mass, kwargs, index=0, quantity=None, search=None, favorite_only=False):
    """Real MA data: AudiobooksController.library_items()/.library_count() -
    same generic MediaControllerBase pattern as get_playlists/get_radio_stations
    above.

    Structurally matches Radio, not Podcasts: there is no chapters()/
    episodes() method on AudiobooksController at all (verified - checked
    for one the same way as PlaylistController.tracks()/
    PodcastsController.episodes() before assuming it existed). An
    audiobook is a single playable item; each row plays directly via its
    own .uri, same uri-based path as Radio/playlist tracks/podcast
    episodes.

    Resume position (fully_played/resume_position_ms) IS real in MA's
    model - its own base_query joins a per-session-user playlog table,
    specifically scoped "so multi-user installs don't surface each
    other's resume state" (that query's own docstring). Our calls here
    have no MA user session attached at all (same as every other
    mass.music.* call in this file - a direct in-process call, not an
    authenticated API request), so these fields will very likely come
    back empty for every item we see here - arguably correct for a
    shared household Squeezebox device that isn't logged in as any
    particular MA user, but untested whether MA's playback layer picks
    up resume position separately at actual play time regardless.

    Real per-book art: icon_id is "audiobook-<item_id>", same namespacing
    pattern as "radio-<id>" - see _fetch_real_item_art.

    windowStyle "icon_list" and reusing tracks_base_actions wholesale are
    both judgment calls here too, same caveat as Radio/Podcasts/
    playlists_base_actions - no real LMS capture of Audiobooks browsing
    anywhere in this project to check against.
    """
    limit = quantity if quantity is not None else 500
    items = await mass.music.audiobooks.library_items(
        limit=limit, offset=index, search=search, summary=False,
        favorite=True if favorite_only else None)
    if search:
        total = len(items)  # library_count() has no search= param - see get_artists' docstring
    else:
        total = await mass.music.audiobooks.library_count(favorite_only=favorite_only)
    item_loop = [
        {
            "text": item.name, "type": "audio", "style": "itemplay", "goAction": "play",
            "commonParams": {"uri": item.uri},
            "icon": f"music/audiobook-{item.item_id}/cover", "icon-id": f"audiobook-{item.item_id}",
            "presetParams": {"favorites_type": "audio", "favorites_title": item.name,
                              "favorites_url": item.uri},
        }
        for item in items
    ]
    return {
        "count": total, "offset": index, "window": {"windowStyle": "icon_list"},
        "base": {"actions": tracks_base_actions(kwargs)},
        "item_loop": item_loop,
    }


async def get_podcasts(mass, kwargs, index=0, quantity=None, search=None, favorite_only=False):
    """Real MA data: PodcastsController.library_items()/.library_count() -
    library_items() IS overridden here (unlike albums/playlists/radio),
    adding favorite/search/genre/provider filtering - but with the same
    limit/offset/summary defaults and semantics for the plain, unfiltered
    call we make here, so it behaves identically to the base-class version
    everything else in this file relies on. library_count() is NOT
    overridden - the plain inherited version, same as always.

    Flat list, no filtering - same shape as get_playlists() above. Real
    per-podcast art via icon_id "podcast-<id>" - see _fetch_real_item_art.
    """
    limit = quantity if quantity is not None else 500
    items = await mass.music.podcasts.library_items(
        limit=limit, offset=index, search=search, summary=False,
        favorite=True if favorite_only else None)
    if search:
        total = len(items)  # library_count() has no search= param - see get_artists' docstring
    else:
        total = await mass.music.podcasts.library_count(favorite_only=favorite_only)
    item_loop = [
        {
            "text": item.name, "type": "playlist",
            "commonParams": {"podcast_id": str(item.item_id)},
            "textkey": item.name[0].upper() if item.name else "",
            "icon": f"music/podcast-{item.item_id}/cover", "icon-id": f"podcast-{item.item_id}",
            "presetParams": {
                "favorites_type": "playlist", "favorites_title": item.name,
                "favorites_url": item.uri,
                "icon": f"music/podcast-{item.item_id}/cover",
            },
        }
        for item in items
    ]
    return {
        "count": total, "offset": index, "window": {"windowStyle": "icon_list"},
        "base": {"actions": playlists_base_actions(kwargs)},
        "item_loop": item_loop,
    }


async def get_podcast_episodes(mass, podcast_id, kwargs, index=0, quantity=None):
    """Real MA data: PodcastsController.episodes(podcast_id, "library") -
    an async generator, same shape and same underlying reason as
    PlaylistController.tracks() above: "podcast episodes are not stored in
    the db/library so we always need to fetch them from the provider"
    (that method's own docstring, verbatim the same claim as playlists) -
    so, same as get_playlist_tracks above: consumed fully into a list
    before paginating (same defensive 2000-item cap, same reasoning), and
    playback uses the episode's own "uri" rather than "track_id", since
    episodes aren't guaranteed to be MA library items any more than
    playlist tracks were.
    """
    items = []
    if podcast_id is not None:
        async for item in mass.music.podcasts.episodes(podcast_id, "library"):
            items.append(item)
            if len(items) >= 2000:  # defensive cap - see docstring above
                break
    window, total, offset = _paginate(items, index, quantity)
    item_loop = [
        {
            "text": item.name, "type": "audio", "style": "itemplay", "goAction": "play",
            "commonParams": {"uri": item.uri},
            "playallParams": {"play_index": offset + i},
            "presetParams": {"favorites_type": "audio", "favorites_title": item.name,
                              "favorites_url": item.uri},
        }
        for i, item in enumerate(window)
    ]
    return {
        "count": total, "offset": offset, "window": {"windowStyle": "text_list"},
        "base": {"actions": tracks_base_actions(kwargs)},
        "item_loop": item_loop,
    }



# Artists/Albums/Tracks/Playlists/Audiobooks/Podcasts/Radio each get
# their own distinct icon name (AllArtists.png, Albums.png, Playlists.png,
# AudioBooks.png, podcasts.png, radiolocal.png) rather than reusing the
# generic "artists.png"/"albums.png" per-row icon names - each needs its
# own real file in static/ (see _resolve_static_icon_path further down,
# which matches these 1:1, no aliasing), even where two of them happen to
# be identical images (Playlists.png is currently a copy of Albums.png,
# and Tracks currently also points at Albums.png - see that tile's own
# comment). Keeping the images in sync (or not) is static/'s problem
# from here, never this list's again.
#
# Ordering/weights here deliberately match Music Assistant's own root UI
# taxonomy (Artists, Albums, Tracks, Playlists, Audiobooks, Podcasts,
# Radio), not real LMS's menu structure - a deliberate choice, not an
# oversight: this repository's actual content model is MA's, and trying
# to keep bending it to fit LMS's own historical categories (which don't
# even distinguish "Album Artists" from "All Artists" in our data - see
# below) was adding confusion without adding fidelity to anything real.
# Genres is the one MA-taxonomy item still missing - not a menu-entry
# oversight, it needs its own real investigation first (MA doesn't model
# genres as a first-class browsable controller the way it does everything
# else here; more like a tag/filter on albums and tracks) - see this
# project's own history for why it was dropped rather than guessed at.


def _standalone_actions(base_actions, item):
    """Build a fully self-contained per-item actions dict from a shared
    base-level template, by baking the item's own data directly into
    each action's params instead of relying on itemsParams to resolve
    it indirectly.

    Real bug found via a direct docker log capture: after get_search_all
    started attaching each row's own matching actions dict directly
    (fixing "nothing happens" on select - see that function's own
    docstring), selecting a track DID fire playlistcontrol - but the
    outgoing request never included track_id, just the shared/static
    params (cmd, sort, search, menu). itemsParams: "commonParams" (the
    mechanism meant to inject the item's own field into the outgoing
    command) never resolved. The SlimBrowse Protocol reference's own
    wording is the likely reason: "In base level commands, this defines
    the name of the field in the item <actions_fields> that must be used
    to complete the command for a particular item" - itemsParams is
    documented as a base-level mechanism specifically; duplicating the
    same template onto each item's own "actions" (rather than the
    response's "base") apparently doesn't get the same resolution.

    Used generally, not just for commonParams - every action in this
    file's four templates (ARTIST_LIST_BASE_ACTIONS/albums_base_actions/
    playlists_base_actions/tracks_base_actions) uses itemsParams,
    referencing one of commonParams, addallParams, or presetParams
    depending on the action - this looks up whichever field each
    individual action actually references on the item itself, rather
    than assuming commonParams everywhere.
    """
    result = {}
    for key, action in base_actions.items():
        new_action = dict(action)
        params_field = new_action.pop("itemsParams", None)
        if params_field and params_field in item:
            new_action["params"] = {**new_action.get("params", {}), **item[params_field]}
        result[key] = new_action
    return result


async def get_search_all(mass, search, kwargs, index=0, quantity=None):
    """Combined search across all seven types at once - queries each type's
    own already-existing get_X() function in parallel (asyncio.gather),
    then concatenates their item_loop rows into one unified list. Reuses
    every bit of each type's own row-building (icon handling, commonParams
    shape, presetParams, playback wiring) entirely - nothing duplicated
    here, this just stitches together what each one already returns.

    Per-type fetch is capped at a fixed, modest amount (15 each, 105 max
    combined) regardless of what index/quantity the device actually
    requested for THIS call - real pagination through the combined set
    happens afterward, locally, via the same _paginate() helper used
    throughout this file for provider-native async-generator results
    (get_playlist_tracks/get_podcast_episodes). Re-querying each of the
    seven types on every single page request (as the device scrolls)
    would be wasteful and wouldn't map cleanly onto a single combined
    index/quantity anyway, since the seven types have no shared ordering.

    "count" reflects however many rows were actually gathered this call
    (up to 105) - not a true total match count across all types, same
    len()-based limitation as each individual type's own search results
    already have (library_count() has no search= param - see get_artists'
    docstring).

    Each row gets its own per-item "actions" block attached explicitly
    (see _standalone_actions() above) rather than relying on this
    response's own "base" the way every other function in this file
    does - there's no single base actions template that correctly
    applies to all seven mixed types at once (an Artist row needs to
    "go" into albums; a Track row needs to "play" directly). Each
    action's own itemsParams-referenced data (track_id, uri, etc.) is
    baked directly into that item's own params rather than left as an
    itemsParams reference - confirmed via a real docker log capture that
    itemsParams doesn't resolve the same way once duplicated onto a
    per-item actions block instead of a genuine response-level "base"
    (matches the SlimBrowse Protocol reference's own description of
    itemsParams as a base-level mechanism specifically).
    """
    per_type_limit = 15
    results = await asyncio.gather(
        get_artists(mass, 0, per_type_limit, search),
        get_albums(mass, None, kwargs, 0, per_type_limit, search),
        get_all_tracks(mass, kwargs, 0, per_type_limit, search),
        get_playlists(mass, kwargs, 0, per_type_limit, search),
        get_audiobooks(mass, kwargs, 0, per_type_limit, search),
        get_podcasts(mass, kwargs, 0, per_type_limit, search),
        get_radio_stations(mass, kwargs, 0, per_type_limit, search),
    )
    # Each type's rows normally rely on "base": {"actions": ...} at the
    # RESPONSE level (a different template per type - see each get_X()
    # function's own return) - their "goAction": "play"/etc. shorthand
    # only resolves because of that shared base. This combined response
    # has no single base that could apply correctly to all seven mixed
    # types at once (an Artist row needs a "go" into albums; a Track row
    # needs to "play" directly - no one template fits both). Fix: attach
    # each row's own correct actions block directly, per item - a real,
    # documented per-item override (<item_fields>.actions in the
    # SlimBrowse Protocol reference). Passed through _standalone_actions
    # (see above) to bake each item's own data directly into its params,
    # rather than reusing the template's itemsParams reference as-is -
    # that stopped resolving once it was no longer at the response's
    # actual "base" (confirmed via a real docker log capture).
    type_actions = [
        ARTIST_LIST_BASE_ACTIONS, albums_base_actions(kwargs), tracks_base_actions(kwargs),
        playlists_base_actions(kwargs), tracks_base_actions(kwargs),
        playlists_base_actions(kwargs), tracks_base_actions(kwargs),
    ]
    combined = []
    for result, actions in zip(results, type_actions, strict=True):
        for item in result["item_loop"]:
            item["actions"] = _standalone_actions(actions, item)
            combined.append(item)
    window, total, offset = _paginate(combined, index, quantity)
    return {
        # icon_list, not text_list - confirmed via a real device report:
        # text_list rendered every row's two-line "text" (title\nartist,
        # same convention get_all_tracks/get_albums already use) as two
        # equal-size lines, looking visibly "off" next to the real Tracks
        # view's title+smaller-subtitle treatment. icon_list already
        # handles exactly this mix correctly elsewhere in this file -
        # two-line rows (get_albums' unfiltered view) and single-line
        # rows (get_playlists/get_radio_stations) both render right under
        # it, and every row here already carries its own "icon" field
        # from whichever type built it, so nothing else needs to change.
        "count": total, "offset": offset, "window": {"windowStyle": "icon_list"},
        "item_loop": window,
    }


async def get_favorites_all(mass, kwargs, index=0, quantity=None):
    """Combined view across all seven favorited types at once - unlike
    get_search_all() above, this does GENUINE pagination across the
    combined set rather than a fixed per-type cap, since a favorites list
    is a real, bounded, curated set someone built up over time (could
    easily be far more than the 15-per-type/105-total cap search results
    settle for) - capping it would quietly hide real favorites rather
    than just under-representing a transient search.

    How: first gets each type's REAL count via that controller's own
    library_count(favorite_only=True) - seven cheap COUNT(*)-only
    queries, run in parallel, no item_loop rows fetched yet. Unlike
    search (library_count() has no search= param - see get_artists'
    docstring), favorite_only genuinely IS a real library_count() param
    (confirmed via the real music-assistant/server source), so this
    total is exact, not a len()-based approximation.

    Those seven counts define a virtual combined range (artists first,
    then albums, tracks, playlists, audiobooks, podcasts, radio - same
    fixed order get_search_all() already uses, kept identical so a type's
    position in "All Favorites" doesn't move relative to where it'd
    appear browsing that type directly). The requested [index, index+
    limit) window is intersected against each type's own slice of that
    virtual range; only types the window actually overlaps get a real
    fetch (their own get_X(favorite_only=True) call, with the LOCAL
    offset/limit inside that type's own slice) - a normal-sized page
    almost always overlaps just one type, occasionally two at a boundary,
    and every fetch that does happen runs concurrently via asyncio.gather.
    A type with zero favorites costs nothing beyond its own count query -
    it never appears in the overlap set at all.

    Row shape reuses everything get_search_all() already established:
    each type's own row-building is untouched, and the same _standalone_
    actions()-per-row treatment applies here for the same reason (no
    single base actions template fits all seven mixed types).
    """
    limit = quantity if quantity is not None else 500
    end = index + limit
    # Fixed order + per-type (count, fetch, actions) - kept in one place so
    # the ordering used for counting/slicing and the ordering used for
    # fetching/rendering can never drift apart.
    type_plan = [
        ("artists",
         mass.music.artists.library_count(favorite_only=True),
         lambda idx, qty: get_artists(mass, idx, qty, None, favorite_only=True),
         ARTIST_LIST_BASE_ACTIONS),
        ("albums",
         mass.music.albums.library_count(favorite_only=True),
         lambda idx, qty: get_albums(mass, None, kwargs, idx, qty, None, favorite_only=True),
         albums_base_actions(kwargs)),
        ("tracks",
         mass.music.tracks.library_count(favorite_only=True),
         lambda idx, qty: get_all_tracks(mass, kwargs, idx, qty, None, favorite_only=True),
         tracks_base_actions(kwargs)),
        ("playlists",
         mass.music.playlists.library_count(favorite_only=True),
         lambda idx, qty: get_playlists(mass, kwargs, idx, qty, None, favorite_only=True),
         playlists_base_actions(kwargs)),
        ("audiobooks",
         mass.music.audiobooks.library_count(favorite_only=True),
         lambda idx, qty: get_audiobooks(mass, kwargs, idx, qty, None, favorite_only=True),
         tracks_base_actions(kwargs)),
        ("podcasts",
         mass.music.podcasts.library_count(favorite_only=True),
         lambda idx, qty: get_podcasts(mass, kwargs, idx, qty, None, favorite_only=True),
         playlists_base_actions(kwargs)),
        ("radio",
         mass.music.radio.library_count(favorite_only=True),
         lambda idx, qty: get_radio_stations(mass, kwargs, idx, qty, None, favorite_only=True),
         tracks_base_actions(kwargs)),
    ]
    counts = await asyncio.gather(*(entry[1] for entry in type_plan))
    total = sum(counts)

    # Intersect the requested [index, end) window against each type's own
    # slice of the combined range, in order, tracking where each type's
    # slice starts as we go.
    overlaps = []  # (fetch_fn, actions, local_index, local_limit)
    cursor = 0
    for (_, _, fetch_fn, actions), count in zip(type_plan, counts, strict=True):
        type_start, type_end = cursor, cursor + count
        cursor = type_end
        overlap_start, overlap_end = max(index, type_start), min(end, type_end)
        if overlap_start < overlap_end:
            overlaps.append((fetch_fn, actions, overlap_start - type_start, overlap_end - overlap_start))

    results = await asyncio.gather(*(
        fetch_fn(local_index, local_limit) for fetch_fn, _, local_index, local_limit in overlaps
    ))
    combined = []
    for (_, actions, _, _), result in zip(overlaps, results, strict=True):
        for item in result["item_loop"]:
            item["actions"] = _standalone_actions(actions, item)
            combined.append(item)
    return {
        # icon_list, not text_list - same real fix and same reasoning as
        # get_search_all() above (see its own comment on this same line).
        "count": total, "offset": index, "window": {"windowStyle": "icon_list"},
        "item_loop": combined,
    }


def _search_category_menu(search):
    """Real LMS's own search UX: typing a term returns a category menu
    (Search All/Artists/Albums/Tracks/.../Radio), each drilling into that
    type's own already-existing browse function, now filtered by search -
    not a separate results path. See the SlimBrowse Protocol reference's
    own documented example (input field + __TAGGEDINPUT__ + a "search"-
    style command) for the client-side mechanism this responds to.

    "Search All" (see get_search_all() above) is listed first - added
    after the fact, once real use showed the seven-way category choice
    got in the way of the common case (wanting everything, not picking a
    type first every time). Kept alongside the per-type options rather
    than replacing them - narrowing to one type is still useful sometimes,
    just no longer the only path.

    Deliberately NOT showing a per-category match count (e.g. "Artists
    (3)", which real LMS's own search does show) - MediaControllerBase.
    library_count() has no search= parameter (checked directly, only
    favorite_only), so a real count per category would mean an extra
    fetch-and-len() per type just to render this menu, for every
    keystroke-driven search. Simpler and cheaper: always list every
    category; a category with no matches just shows an empty list on
    drill-in, same as browsing to a genuinely empty part of the library
    today - not treated as an error state anywhere else in this file
    either.
    """
    categories = [
        ("Search All", "search_all"),
        ("Artists", "artists"), ("Albums", "albums"), ("Tracks", "tracks"),
        ("Playlists", "playlists"), ("Audiobooks", "audiobooks"),
        ("Podcasts", "podcasts"), ("Radio", "radio"),
    ]
    item_loop = [
        {
            "text": label, "type": "playlist",
            "actions": {"go": {"cmd": ["browselibrary", "items"],
                                "params": {"menu": 1, "mode": submode, "search": search}}},
        }
        for label, submode in categories
    ]
    return {
        "count": len(item_loop), "offset": 0, "window": {"windowStyle": "text_list"},
        "item_loop": item_loop,
    }


def _favorites_category_menu():
    """Mirrors _search_category_menu() above exactly - same category-menu
    shape (All Favorites first, then one entry per type), same reasoning
    for why: a flat "everything" view is the common case, per-type still
    useful sometimes. The one real difference from search: favorite_only
    genuinely DOES support library_count() (confirmed via the real
    music-assistant/server source - only search= is unsupported there),
    so unlike _search_category_menu, a real per-category count is cheap
    enough to show here (e.g. "Albums (12)") - kept out anyway, for now,
    to keep this a straight, obviously-correct mirror of the existing
    pattern rather than a divergent one; revisit if that count turns out
    to matter in practice.

    Each entry reuses the exact same mode values that already exist -
    "favorites_all"/"artists"/"albums"/etc. - with favorite_only: 1
    threaded through instead of search: this is the same dispatch branch
    every other browse already goes through (see _dispatch below), not a
    parallel favorites-specific path.
    """
    categories = [
        ("All Favorites", "favorites_all"),
        ("Artists", "artists"), ("Albums", "albums"), ("Tracks", "tracks"),
        ("Playlists", "playlists"), ("Audiobooks", "audiobooks"),
        ("Podcasts", "podcasts"), ("Radio", "radio"),
    ]
    item_loop = [
        {
            "text": label, "type": "playlist",
            "actions": {"go": {"cmd": ["browselibrary", "items"],
                                "params": {"menu": 1, "mode": submode, "favorite_only": 1}}},
        }
        for label, submode in categories
    ]
    return {
        "count": len(item_loop), "offset": 0, "window": {"windowStyle": "text_list"},
        "item_loop": item_loop,
    }


MY_MUSIC_NODE = [
    {"node": "home", "id": "myMusic", "text": "My Music", "isANode": 1, "weight": 11},
    {
        # Was two separate tiles (Album Artists / All Artists), collapsing
        # real LMS's role-based artist/album-artist distinction - removed
        # since get_artists() above never modeled that distinction at all
        # (no role_id filtering), so the two tiles always returned the
        # identical list under different names. One honest tile instead.
        # Icon reuses AllArtists.png rather than requiring a new file -
        # semantically the closer match now that this shows every artist
        # uniformly, and this project's existing 1:1-filename discipline
        # holds: change this line if you'd rather it point at its own file.
        "node": "myMusic", "id": "myMusicArtists", "text": "Artists",
        "homeMenuText": "Browse Artists", "icon": "html/images/AllArtists.png", "weight": 10,
        "actions": {"go": {"cmd": ["browselibrary", "items"], "params": {"menu": 1, "mode": "artists"}}},
    },
    {
        "node": "myMusic", "id": "myMusicAlbums", "text": "Albums",
        "homeMenuText": "Browse Albums", "icon": "html/images/Albums.png", "weight": 20,
        "actions": {"go": {"cmd": ["browselibrary", "items"], "params": {"menu": 1, "mode": "albums"}}},
    },
    {
        # Real data now - see get_all_tracks() above. New alongside this
        # menu restructure - previously tracks were only reachable by
        # drilling into an album first, no flat root-level browse existed.
        # Icon reuses Albums.png - no dedicated "tracks" file exists, and
        # none was given for this one either. Same deal as the Audiobooks
        # entry below: rename this line once a real one shows up.
        "node": "myMusic", "id": "myMusicTracks", "text": "Tracks",
        "homeMenuText": "Browse Tracks", "icon": "html/images/Albums.png", "weight": 30,
        "actions": {"go": {"cmd": ["browselibrary", "items"], "params": {"menu": 1, "mode": "tracks"}}},
    },
    {
        # Real data now - see get_playlists() above.
        "node": "myMusic", "id": "myMusicPlaylists", "text": "Playlists",
        "homeMenuText": "Playlists", "icon": "html/images/Playlists.png", "weight": 40,
        "actions": {"go": {"cmd": ["browselibrary", "items"], "params": {"menu": 1, "mode": "playlists"}}},
    },
    {
        # Real data now - see get_audiobooks() above.
        "node": "myMusic", "id": "myMusicAudiobooks", "text": "Audiobooks",
        "homeMenuText": "Audiobooks", "icon": "html/images/AudioBooks.png", "weight": 50,
        "actions": {"go": {"cmd": ["browselibrary", "items"], "params": {"menu": 1, "mode": "audiobooks"}}},
    },
    {
        # Real data now - see get_podcasts()/get_podcast_episodes() above.
        "node": "myMusic", "id": "myMusicPodcasts", "text": "Podcasts",
        "homeMenuText": "Podcasts", "icon": "html/images/podcasts.png", "weight": 60,
        "actions": {"go": {"cmd": ["browselibrary", "items"], "params": {"menu": 1, "mode": "podcasts"}}},
    },
    {
        # Real data now - see get_radio_stations() above. Distinct from
        # real LMS's own "Radio" node, which is normally a TuneIn/plugin
        # browse-and-discover experience - this is specifically your MA
        # library's saved radio stations, playable directly (see that
        # function's own docstring for why there's no sub-list to drill
        # into first).
        "node": "myMusic", "id": "myMusicRadio", "text": "Radio",
        "homeMenuText": "Radio", "icon": "html/images/radiolocal.png", "weight": 70,
        "actions": {"go": {"cmd": ["browselibrary", "items"], "params": {"menu": 1, "mode": "radio"}}},
    },
    {
        # Real search now - see _search_category_menu() above.
        # "input": {"len": 3} is the structured <input_fields> form from
        # the SlimBrowse Protocol reference's own schema - the doc's
        # illustrative example shows a bare "input = 3" shorthand, but
        # that's Perl's loose typing, not necessarily something the
        # client's JSON parser accepts the same way; using the
        # schema's own documented object form here instead.
        # __TAGGEDINPUT__ is replaced client-side with whatever the user
        # typed, sent back as search:<what they typed> - by the time this
        # reaches _dispatch, kwargs["search"] is already the plain query
        # string, nothing further to decode here.
        "node": "myMusic", "id": "myMusicSearch", "text": "Search",
        "homeMenuText": "Search", "weight": 80,
        "input": {"len": 3},
        "actions": {"go": {"cmd": ["browselibrary", "items"],
                            "params": {"menu": 1, "mode": "search", "search": "__TAGGEDINPUT__"}}},
    },
    {
        # v45: implemented for real - see _favorites_category_menu()/
        # get_favorites_all() above. Was previously in the "Deliberately
        # NOT included" list below on the grounds that it looked like its
        # own real subsystem - turned out to just be a real, existing
        # favorite=/favorite_only= filter every media controller's
        # library_items()/library_count() already supports (confirmed
        # against the real music-assistant/server source), the same
        # shape as every other tile here. Weight 5 (ahead of everything
        # else) - a deliberate placement choice, not verified against a
        # real LMS capture of where Favorites sits in its own home menu;
        # move this if a real capture turns out to disagree.
        #
        # icon (v47): plain unsized name, matching every sibling tile
        # here (AllArtists.png, not AllArtists_225x225_m.png) -
        # _resolve_static_icon_path (below) strips the device's own
        # requested size suffix and looks for a real file named
        # favorites_225x225_m.png first, falling back to favorites.png -
        # needs one of those two real files placed in static/, same as
        # every other chrome icon in this file.
        "node": "myMusic", "id": "myMusicFavorites", "text": "Favorites",
        "homeMenuText": "Favorites", "icon": "html/images/favorites.png", "weight": 5,
        "actions": {"go": {"cmd": ["browselibrary", "items"], "params": {"menu": 1, "mode": "favorites"}}},
    },
    # Deliberately NOT included: Settings, Random Mix, and any
    # plugin-contributed entries (TuneIn's own browse/discover
    # experience, etc.) - these map to entirely separate MA subsystems
    # (TuneIn integration, settings screens), each its own real piece of
    # work, not something to add here without review. A real LMS home
    # menu has ~50 items along these lines; this intentionally stays
    # narrow to what's actually implemented behind it.
]


def _build_preset_items(player):
    """Replicates aioslimproto's own built-in preset-menu logic exactly
    (see its _handle_menu), since taking over 'menu' ourselves to add the
    My Music node means the built-in preset handling is bypassed entirely
    unless we redo it ourselves too - not calling into aioslimproto's
    private internals, just matching its own already-verified shape."""
    items = []
    for index, preset in enumerate(getattr(player, "presets", []) or []):
        preset_id = f"preset_{index + 1}"
        items.append({
            "id": preset_id, "icon": preset.icon, "text": preset.text,
            "homeMenuText": preset.text, "weight": 35, "node": "myMusic",
            "style": "itemplay", "nextWindow": "nowPlaying",
            "actions": {
                "go": {"cmd": ["button", f"{preset_id}.single"], "itemsParams": "commonParams",
                       "params": {}, "player": 0, "nextWindow": "nowPlaying"},
                "add": {"player": 0, "itemsParams": "commonParams",
                        "params": {"uri": preset.uri, "cmd": "add"},
                        "cmd": ["playlistcontrol"], "nextWindow": "refresh"},
                "more": {"player": 0, "itemsParams": "commonParams",
                         "params": {"uri": preset.uri, "cmd": "add"},
                         "cmd": ["playlistcontrol"], "nextWindow": "refresh"},
                "play": {"cmd": ["playlistcontrol"], "itemsParams": "commonParams",
                         "params": {"uri": preset.uri, "cmd": "play"},
                         "player": 0, "nextWindow": "nowPlaying"},
                "play-hold": {"cmd": ["playlistcontrol"], "itemsParams": "commonParams",
                              "params": {"uri": preset.uri, "cmd": "load"},
                              "player": 0, "nextWindow": "nowPlaying"},
                "add-hold": {"itemsParams": "commonParams", "params": {"uri": preset.uri, "cmd": "insert"},
                             "player": 0, "cmd": ["playlistcontrol"], "nextWindow": "refresh"},
            },
        })
    return items


def get_menu(player, index=0, quantity=100):
    item_loop = list(MY_MUSIC_NODE) + _build_preset_items(player)
    window, total, offset = _paginate(item_loop, index, quantity)
    return {"item_loop": window, "offset": offset, "count": total}


class BrowseLibraryHandler:
    """cli_command_handler for SlimServer - handles 'browselibrary' and
    'menu', raising NotImplementedError for everything else so it falls
    through to aioslimproto's built-ins untouched (status, serverstatus,
    etc.).

    Takes the whole provider (not just mass) since 'menu' needs
    provider.slimproto.get_player() for presets, alongside provider.mass
    for real library data later - and provider.slimproto doesn't exist
    yet at the moment this handler is constructed (it's one of
    SlimServer's own constructor args), so it's accessed lazily through
    the provider at call time, not stored directly here.
    """

    def __init__(self, provider):
        self.provider = provider
        self.mass = provider.mass

    async def __call__(self, slim_command):
        # print(), not logger - guaranteed to show in `docker logs`
        # regardless of MA's configured log level (VERBOSE vs DEBUG has
        # been confusing to get reliably right in practice). Every line
        # prefixed "[BL]" so it's easy to grep out of the full log.
        #
        # __call__/_dispatch are async now that get_artists/get_albums/
        # get_tracks make real (awaited) mass.music.* calls - aioslimproto's
        # own dispatch (_dispatch_command in cli.py) already handles an
        # awaitable command_handler result via inspect.isawaitable(), so no
        # change was needed there. Keeping the try/except here (rather than
        # letting it return an un-awaited coroutine for the caller to await
        # unguarded) matters: it's what keeps a real MA-side exception
        # (MediaNotFoundError, a DB error, etc.) caught and printed with full
        # context right here, instead of surfacing bare at whatever distant
        # point happens to await this - same reasoning as the existing
        # "QUEUE FULL" and 30s-wait forced-print instrumentation elsewhere
        # in this project: a real failure should be impossible to miss.
        print(f"[BL] __call__ ENTER command={slim_command.command!r} "
              f"player_id={slim_command.player_id!r} args={slim_command.args!r} "
              f"kwargs={slim_command.kwargs!r}", flush=True)
        try:
            result = await self._dispatch(slim_command)
            print(f"[BL] __call__ EXIT command={slim_command.command!r} -> "
                  f"count={result.get('count') if isinstance(result, dict) else '?'}", flush=True)
            return result
        except NotImplementedError:
            print(f"[BL] __call__ EXIT command={slim_command.command!r} -> "
                  f"NotImplementedError (falling through to aioslimproto's built-in)", flush=True)
            raise  # the intentional "let aioslimproto's built-in handle this" signal - not an error
        except Exception:
            import traceback
            print(f"[BL] __call__ EXCEPTION command={slim_command.command!r} "
                  f"player_id={slim_command.player_id!r}:\n{traceback.format_exc()}", flush=True)
            raise

    async def _dispatch(self, slim_command):
        if slim_command.command == "menu":
            player = self.provider.slimproto.get_player(slim_command.player_id)
            if player is None:
                raise NotImplementedError  # unknown player - let the built-in handle/reject it
            return get_menu(player)  # no real data involved - stays sync, just not awaited

        if slim_command.command == "playlistcontrol":
            return await self._handle_playlistcontrol(slim_command)

        if slim_command.command == "trackinfo":
            return await self._handle_trackinfo(slim_command)

        if slim_command.command == "contextmenu":
            return await self._handle_contextmenu(slim_command)

        if slim_command.command == "playlist":
            return await self._handle_playlist(slim_command)

        if slim_command.command == "jiveblankcommand":
            # Real LMS's own no-op command (Slim/Control/Queries.pm's
            # _addJivePlaylistControls, confirmed via the real source) -
            # what the queue view's "Clear Playlist" submenu's "Cancel"
            # row sends. Not handled anywhere in aioslimproto (checked
            # before adding this - no _handle_jiveblankcommand exists
            # there either), so without this it would raise
            # NotImplementedError and surface as a real error to the
            # device for a plain "never mind" tap. A true no-op: nothing
            # to do, nothing to confirm.
            return None

        if slim_command.command == "status":
            return await self._handle_queue_status(slim_command)

        if slim_command.command != "browselibrary":
            raise NotImplementedError

        args = slim_command.args
        kwargs = slim_command.kwargs
        if not args or args[0] != "items":
            logger.warning("browselibrary: unexpected args=%r", args)
            raise NotImplementedError

        index = int(args[1]) if len(args) > 1 else 0
        quantity = int(args[2]) if len(args) > 2 else None
        mode = kwargs.get("mode")
        search = kwargs.get("search")
        # v45: same idea as search= above, threaded through every existing
        # per-type branch below rather than adding a parallel set of
        # favorites-specific modes - "Favorites > Albums" and "My Music >
        # Albums" both end up calling the exact same get_albums(), just
        # with this one extra flag set. bool(...) since SlimBrowse params
        # arrive as ints (favorite_only: 1), not real booleans.
        favorite_only = bool(kwargs.get("favorite_only"))

        if mode == "search":
            # The "Search" home menu item itself (see MY_MUSIC_NODE) -
            # returns a category menu (Search All/Artists/Albums/Tracks/
            # .../Radio), each entry re-entering with that same search
            # term plus its own mode - reusing every existing get_X()
            # function's new search= support below rather than a
            # separate results path.
            return _search_category_menu(search or "")
        if mode == "search_all":
            return await get_search_all(self.mass, search or "", kwargs, index, quantity)
        if mode == "favorites":
            # The "Favorites" home menu item (see MY_MUSIC_NODE) - same
            # category-menu shape as "search" above, see
            # _favorites_category_menu()'s own docstring for how the two
            # compare.
            return _favorites_category_menu()
        if mode == "favorites_all":
            return await get_favorites_all(self.mass, kwargs, index, quantity)
        if mode == "artists":
            return await get_artists(self.mass, index, quantity, search, favorite_only)
        if mode == "albums":
            artist_id = kwargs.get("artist_id")
            artist_id = str(artist_id) if artist_id is not None else None
            return await get_albums(
                self.mass, artist_id, kwargs, index, quantity, search, favorite_only)
        if mode == "tracks":
            # podcast_id / playlist_id / album_id checked first: if
            # present, this is that container's episode/track listing -
            # each of those paths is completely unchanged/untouched by
            # this addition, so nothing about already-working playback
            # for any of them is at risk here. No context id at all means
            # the root-level "Tracks" browse (get_all_tracks) - previously
            # a dead end here (get_tracks with album_id=None just returns
            # an empty list). None of the three container-scoped paths
            # take a search= or favorite_only= param - both only apply to
            # the flat, root-level browse, same reasoning as get_albums'
            # artist_id branch above.
            podcast_id = kwargs.get("podcast_id")
            if podcast_id is not None:
                return await get_podcast_episodes(self.mass, str(podcast_id), kwargs, index, quantity)
            playlist_id = kwargs.get("playlist_id")
            if playlist_id is not None:
                return await get_playlist_tracks(self.mass, str(playlist_id), kwargs, index, quantity)
            album_id = kwargs.get("album_id")
            if album_id is not None:
                return await get_tracks(self.mass, str(album_id), kwargs, index, quantity)
            return await get_all_tracks(self.mass, kwargs, index, quantity, search, favorite_only)
        if mode == "playlists":
            return await get_playlists(self.mass, kwargs, index, quantity, search, favorite_only)
        if mode == "radio":
            return await get_radio_stations(self.mass, kwargs, index, quantity, search, favorite_only)
        if mode == "podcasts":
            return await get_podcasts(self.mass, kwargs, index, quantity, search, favorite_only)
        if mode == "audiobooks":
            return await get_audiobooks(self.mass, kwargs, index, quantity, search, favorite_only)

        logger.warning("browselibrary: unhandled mode=%r", mode)
        raise NotImplementedError

    async def _handle_playlistcontrol(self, slim_command):
        """
        Basic first pass at playlistcontrol - the command JiveLite sends when
        the user picks play/add/insert on a browselibrary item (see the "go"/
        "play"/"add"/"add-hold" actions in tracks_base_actions above).

        Handles cmd=load (play now), cmd=add (append to end of the queue),
        and cmd=insert (play next) - see the queue_options mapping just
        below for the cmd -> QueueOption pairing. Either via one of two tags:
          - "uri" - used by playlist tracks (get_playlist_tracks above),
            since those aren't guaranteed to be MA library items (see that
            function's own docstring) - played directly via the uri, no
            lookup needed. Checked first since it's the cheaper, more
            general path when present.
          - "track_id" - used by album tracks (get_tracks above), which
            genuinely are library items - resolved via
            mass.music.tracks.get_library_item(track_id) for its `.uri`.
            Unchanged from before "uri" support was added - an album
            track row never sends a "uri" tag, so this path behaves
            identically to before for the (far more common) album case.
        Either way, mapped onto mass.player_queues.play_media(queue_id=
        player_id, media=<uri>, option=<mapped QueueOption>) - verified
        against the real PlayerQueuesController source (music_assistant/
        controllers/player_queues/controller.py): queue_id is documented
        as literally the player_id, and media accepts a plain URI string
        directly.

        As of v36, cmd=add/insert also triggers a real, immediate push
        to any subscribed queue-view screen, instead of leaving it to
        wait for aioslimproto's own ~60s periodic subscription replay -
        see the inline comment right before the push call for the full
        reasoning and the one unconfirmed assumption it rests on
        (a subscription response-channel key match).

        NOT yet handled, same "basic first pass" scope as the rest of this
        integration - falls through to NotImplementedError (today's built-in
        behavior: published as an unhandled event, a safe no-op, not a
        regression) rather than silently acking a request we can't fulfill:
          - any cmd other than load/add/insert
          - album_id/artist_id/playlist_id-driven playlistcontrol
            (playing/queueing a whole album, artist, or playlist rather
            than one track)
          - real LMS's "play the whole album starting at play_index" semantics
            for a track row specifically - see tracks_base_actions' own
            comment on why that's not wired up yet either
        """
        # cmd -> QueueOption mapping. Real member names confirmed directly
        # against music_assistant_models.enums.QueueOption in the running
        # container before writing this, not guessed: PLAY, REPLACE, NEXT,
        # REPLACE_NEXT, ADD, UNKNOWN.
        # "load" keeps its existing, already-verified-working PLAY
        # semantics unchanged.
        # "add" -> ADD (append to end of queue) is a direct name match.
        # "insert" -> NEXT (play next, without interrupting whatever's
        # currently playing) matches real LMS's own "insert" semantics by
        # behavior, not as literal a name match as add -> ADD - worth a
        # real-device confirmation: queue something, use "Play Next" on a
        # second item while the first is still playing, and check it
        # doesn't jump the gun and interrupt.
        queue_options = {
            "load": QueueOption.PLAY,
            "add": QueueOption.ADD,
            "insert": QueueOption.NEXT,
        }
        kwargs = slim_command.kwargs
        cmd = kwargs.get("cmd")
        player_id = slim_command.player_id
        queue_option = queue_options.get(cmd)
        if queue_option is None or player_id is None:
            raise NotImplementedError

        if (uri := kwargs.get("uri")) is not None:
            print(f"[BL] playlistcontrol: cmd={cmd!r} uri={uri!r} on "
                  f"player_id={player_id!r}", flush=True)
            media = uri
        else:
            track_id = kwargs.get("track_id")
            if track_id is None:
                raise NotImplementedError
            # aioslimproto's parse_args coerces numeric-looking tag values
            # to int (see _context's own comment above) - MediaControllerBase.
            # get_library_item accepts int | str, but str() it for the
            # print below regardless of which one we got.
            track = await self.mass.music.tracks.get_library_item(track_id)
            print(f"[BL] playlistcontrol: cmd={cmd!r} track_id={track_id!r} "
                  f"({track.name!r}) on player_id={player_id!r}", flush=True)
            # media=track.uri (a bare string), matching every other
            # play_media call in this file (playlist tracks/radio/
            # podcasts/audiobooks/episodes all pass a plain uri) - NOT
            # media=track (the resolved object), which this briefly was
            # as a candidate fix for the client-side pause bug. Tested
            # directly and ruled out: the real fix turned out to be
            # reinject.sh's pinned aioslimproto version (was silently
            # stuck on 3.2.0, missing real STAT/RESP ordering fixes that
            # shipped in 3.2.1/3.2.2) - unrelated to this call entirely.
            # Reverted to the uri form for consistency now that the
            # object-passing theory has a disproven, no-longer-relevant
            # reason to be a special case.
            media = track.uri

        await self.mass.player_queues.play_media(
            queue_id=player_id, media=media, option=queue_option,
        )

        if cmd in ("add", "insert"):
            # Real, event-driven push for the queue-view screen -
            # cmd="load" doesn't need this: it already changes what's
            # actually playing, which triggers aioslimproto's own real
            # PLAYER_UPDATED event through the normal playback path (see
            # player.py), and that already pushes any subscribed
            # queue-view screen on its own. add/insert change the queue
            # without changing playback, so nothing would otherwise
            # trigger a push until aioslimproto's built-in periodic
            # replay loop (_do_periodic, cli.py) got to it on its own
            # fixed 60-second cycle - confirmed by reading that loop's
            # real source before relying on it, not guessed.
            await self._push_queue_update(player_id)

        return None

    async def _push_queue_update(self, player_id):
        """Push a real, immediate queue-view update to any subscribed
        screen - factored out of _handle_playlistcontrol (v36-v39) as of
        v50, since the new playlist jump/delete/move/clear handlers below
        need the exact same real push after every queue-mutating action,
        not just add/insert.
        """
        # TWO pushes, not one - confirmed necessary via a real
        # device test after v36 shipped just the first: the header
        # ("Playing X of Y") updated immediately, but the scrollable
        # queue-view LIST itself still didn't, until the next real
        # track change. Root cause, found by reading cli.py's other
        # real subscription-push example (PLAYER_PRESETS_UPDATED)
        # side by side with what v36 was doing: that's the only
        # place in aioslimproto that successfully pushes a live list
        # update, and it does NOT go through _handle_cometd_client_
        # request (a generic replayed-request response) - it builds
        # a specific ["replace"] instruction and puts it directly on
        # the client's queue. v36's _on_player_event(PLAYER_UPDATED)
        # call only does the former, which is apparently sufficient
        # for scalar header fields but not for triggering the list
        # widget's own live redraw.
        #
        # Push 1 (unchanged from v36) - reuses aioslimproto's own
        # _on_player_event (cli.py) instead of reinventing
        # subscription-push logic: looks up the CometD client
        # subscribed for this player_id, and if it has a stored
        # playerstatus subscription (which is what the queue-view
        # screen registers on open, since it sends subscribe:600),
        # immediately replays that stored request and pushes the
        # result - the same call the periodic loop makes, just
        # triggered here instead of by a timer. Confirmed via a real
        # trace through both event systems that _on_player_event is
        # private to aioslimproto's CometD/CLI layer only
        # (SlimProtoCLI) - entirely separate from
        # SlimServer.subscribe()'s own event bus (what provider.py's
        # _handle_slimproto_event listens on) - so this has no other
        # side effects beyond the intended push. The channel-key
        # assumption this rests on (f"/{{client_id}}/slim/
        # playerstatus/{{player_id}}", the same hardcoded pattern
        # _on_player_event's own PLAYER_UPDATED branch already uses)
        # IS now confirmed correct via a real device test: the
        # header did update, meaning the push reached the right
        # subscription.
        cli = self.provider.slimproto.cli
        await cli._on_player_event(
            SlimEvent(type=EventType.PLAYER_UPDATED, player_id=player_id))

        # Push 2 (new) - the actual list-replace push, built by
        # copying cli.py's PLAYER_PRESETS_UPDATED push verbatim in
        # shape (same "channel"/"id"/"data"/"ext" structure, same
        # [player_id, item_loop, "replace", player_id] data array,
        # same client.queue.put_nowait + QueueFull suppression) -
        # substituting our own real queue item_loop
        # (_build_queue_item_loop, the same helper _handle_
        # queue_status itself uses) for the presets menu the
        # original passes. Looks up the same subscription key push 1
        # already confirmed correct, rather than re-deriving it.
        #
        # offset/limit for the rebuild: reads them from the
        # SUBSCRIBED request's own stored args (sub["data"]
        # ["request"][1]), not hardcoded - the client's original
        # subscribe call is the only real source of what window it
        # actually wants kept in sync live, and this deliberately
        # matches _handle_queue_status's own args-parsing logic
        # (args[0]=offset, args[1]=limit, "-" meaning "anchor on the
        # current queue position") rather than inventing a second,
        # possibly-inconsistent interpretation.
        #
        # Confirmed working via a real device test (v38/v39) for the
        # add/insert case; the jump/delete/move/clear callers added in
        # v50 reuse this same push unmodified.
        client = next(
            (x for x in cli._cometd_clients.values() if x.player_id == player_id),
            None,
        )
        if client is not None and (
            sub := client.slim_subscriptions.get(
                f"/{client.client_id}/slim/playerstatus/{player_id}",
            )
        ):
            queue = self.mass.player_queues.get_active_queue(player_id)
            if queue is not None:
                # sub["data"]["request"][1] is the FULL inner array
                # INCLUDING the command name - real captures confirm
                # the stored shape is ['status', '-', 10,
                # 'menu:menu', ...], not just the offset/limit args
                # on their own (matches the documented real
                # /slim/subscribe format: request => ['',
                # ['serverstatus', 0, 50, 'subscribe:60']]).
                # sub_args[0] is therefore the command name itself
                # ('status'), offset is at [1], limit at [2] - a
                # real off-by-one bug in the first version of this
                # push, confirmed via a real traceback (ValueError:
                # invalid literal for int() with base 10: '-' -
                # attempting int() on the offset string it had
                # mistaken for the limit).
                sub_args = sub["data"]["request"][1][1:]
                raw_offset = sub_args[0] if len(sub_args) > 0 else "-"
                sub_limit = int(sub_args[1]) if len(sub_args) > 1 else 10
                sub_offset = (
                    queue.current_index or 0
                    if raw_offset == "-"
                    else int(raw_offset)
                )
                item_loop = await self._build_queue_item_loop(
                    queue, sub_offset, sub_limit)
                print(f"[BL] queue push: player_id={player_id!r} "
                      f"offset={sub_offset!r} limit={sub_limit!r} "
                      f"returned={len(item_loop)!r}", flush=True)
                with suppress(asyncio.QueueFull):
                    client.queue.put_nowait(
                        {
                            "channel": sub["data"]["response"],
                            "id": sub["id"],
                            "data": [player_id, item_loop, "replace", player_id],
                            "ext": {"priority": sub["data"].get("priority")},
                        },
                    )
        return None

    async def _handle_trackinfo(self, slim_command):
        """

        Handles the "more" action's trackinfo/items command - what JiveLite
        actually sends on a long-press of a track row, per tracks_base_actions'
        own "more" entry (cmd: ["trackinfo", "items"]). This was previously
        entirely unhandled: _dispatch fell through to NotImplementedError for
        any command other than menu/playlistcontrol/browselibrary, so a
        long-press got no response at all - a blank screen, not an error
        (confirmed via a real docker log capture: command='trackinfo'
        args=['items', 0, 200] kwargs={..., 'track_id': 18, ...} ->
        NotImplementedError every time).

        IMPORTANT - NOT verified against a real LMS trackinfo capture, same
        caveat playlists_base_actions already flags for itself: this returns
        just the three playback options (Play/Add/Play Next), built by
        analogy to tracks_base_actions' own "play"/"add"/"add-hold" entries
        (same cmd/params shape), not real LMS's actual trackinfo menu (which
        also has non-playback rows - credits, "more from this artist",
        genre, etc. - backed by real metadata this project doesn't fetch).
        If a real capture ever shows a different shape for the rows
        themselves (text, "type", "nextWindow" specifics), this is the
        function to fix.

        kwargs already has the item's own track_id/uri flattened directly in
        (see the log capture above) - aioslimproto resolves itemsParams
        before calling this handler, the same as it does for browselibrary/
        menu commands. No _standalone_actions()-style baking needed here the
        way Search All rows needed it: this is one fixed 3-row menu built
        fresh per request, not a template shared across many item_loop rows.
        """
        kwargs = slim_command.kwargs
        track_id = kwargs.get("track_id")
        uri = kwargs.get("uri")
        base_params = {"track_id": track_id} if track_id is not None else {"uri": uri}

        def _row(text, cmd):
            return {
                "text": text,
                "type": "text",
                "style": "item",
                "actions": {
                    "go": {
                        "player": 0,
                        "cmd": ["playlistcontrol"],
                        "params": {**base_params, "cmd": cmd},
                    },
                },
                # "load" replaces the queue and starts playing - jump to
                # Now Playing, same as every other "play" action in this
                # file. "add"/"insert" don't interrupt anything currently
                # playing, so there's nothing to jump to - close back out
                # to the browse list instead.
                "nextWindow": "nowPlaying" if cmd == "load" else "parent",
            }

        item_loop = [
            _row("Play Song", "load"),
            _row("Add to End of Queue", "add"),
            _row("Play Next", "insert"),
        ]
        print(f"[BL] trackinfo: menu for track_id={track_id!r} uri={uri!r}", flush=True)
        return {
            "count": len(item_loop),
            "offset": 0,
            # windowStyle: "text_list", NOT isContextMenu:1 - confirmed
            # via a real pcap capture of an actual context-menu RESPONSE
            # (LMS 9.1.1): the real response's own top-level "window" is
            # {"windowStyle": "text_list"}. isContextMenu:1 IS real, but
            # belongs on the ACTION that navigates here (base.actions.
            # more's own "window" field, confirmed in the same capture -
            # see MY_MUSIC_NODE-area action defs elsewhere in this file
            # for where that's legitimately used), not on the response
            # data itself. Mixing the two up here is a real, confirmed
            # bug - not verified working before now, since nothing had
            # compared this against real captured data until the queue
            # context-menu work surfaced it.
            "window": {"windowStyle": "text_list"},
            "item_loop": item_loop,
        }

    async def _handle_contextmenu(self, slim_command):
        """Handles the "more" action's contextmenu command - what a
        long-press on a QUEUE row sends, as distinct from trackinfo
        (library rows). Confirmed via the real slimserver source
        (Slim/Control/Queries.pm's own statusQuery(), useContextMenu
        branch): base.actions.more.cmd is ['contextmenu'], not
        ['trackinfo', 'items'], for the queue view specifically -
        matched here by aioslimproto's own built-in _handle_status,
        confirmed by reading its real source before writing this: it
        already sets base.actions.more exactly this way (cmd:
        ["contextmenu"], params: {"context": "playlist", ...}) for the
        menu='menu' queue view, unchanged by _handle_queue_status above.

        Was previously entirely unhandled - not just by this file, by
        aioslimproto too (no _handle_contextmenu exists there either,
        confirmed before writing this) - so every long-press on a queue
        row has been raising NotImplementedError this whole time,
        regardless of anything else in this session's queue work.

        Only handles context == "playlist" (the queue-row case this was
        built for) - anything else raises NotImplementedError, same
        conservative scoping as every other "basic first pass" handler
        in this file, since there's no other real contextmenu caller to
        verify a shape against yet.

        playlist_index comes from the row's own params - added to every
        queue row in _build_queue_item_loop above specifically for this
        (itemsParams: "params" on base.actions.more means the pressed
        row's own params get merged into this request - confirmed via
        aioslimproto's real base.actions.more source, not guessed).

        Row shape and real commands below are pcap-verified against a
        genuine LMS 9.1.1 capture of this exact menu (long-pressing a
        queue row, picking "Remove from playlist") - not reconstructed
        from TrackInfo.pm as the first version of this was. Real,
        confirmed pattern: EVERY relevant action key on a row
        (play/go/add/add-hold) maps to the exact same command - so
        whichever gesture JiveLite resolves a tap or hold to, the same
        thing happens. Real full menu also includes "Save to Favorites"
        and several generic drill-down entries (Album Artist, Album,
        Genre, ...) - deliberately left out here per direct instruction
        (only Remove/Play Next/Play wanted), not because they're hard.
          - "Remove from playlist" -> playlist delete <index>.
            Pcap-confirmed text, command, and the all-four-keys pattern
            exactly. Real LMS never skips this one - MA's own
            delete_item() already guards the one real edge case
            (deleting an item already loaded in the playback buffer) by
            silently no-op'ing with its own log warning, so there's
            nothing extra to check here either.
          - "Play Next" -> playlist move <index>. NOT present in the
            captured pcap (the specific track/position tested didn't
            show it - confirmed by its real absence from that capture),
            so the exact command args are inferred from the same
            all-four-keys pattern the other two rows prove, using the
            same playlist/move handling _handle_playlist already
            implements (pos_shift=0 - "move to front of upcoming
            items"). Skipped when this row is already the current or
            immediately-next track, matching the same reasoning
            move_item's own real pos_shift=0 case already encodes -
            there's nothing to usefully move to.
          - "Play" -> playlist jump <index>. Pcap-confirmed text,
            command, and pattern exactly, including addAction/style.
            Skipped (matching real LMS's own check) only if this row IS
            the current track AND it's actively playing right now -
            resuming a paused current track is still a meaningful
            action, so only PlaybackState.PLAYING (not PAUSED) skips
            this.
        """
        kwargs = slim_command.kwargs
        player_id = slim_command.player_id
        if kwargs.get("context") != "playlist":
            raise NotImplementedError

        playlist_index = kwargs.get("playlist_index")
        if playlist_index is None:
            raise NotImplementedError
        playlist_index = int(playlist_index)

        queue = self.mass.player_queues.get_active_queue(player_id)
        if queue is None:
            raise NotImplementedError
        current_index = queue.current_index or 0
        is_current_and_playing = (
            playlist_index == current_index and queue.state == PlaybackState.PLAYING
        )

        def _row(text, cmd, style=None):
            # Real, pcap-confirmed shape: the SAME action def repeated
            # under all four keys - not just "go". addAction:"go" is
            # itself part of the real captured shape (present on every
            # real row here), not something this project invented.
            action = {"cmd": cmd, "player": 0, "nextWindow": "parent"}
            row = {
                "text": text,
                "type": "text",
                "addAction": "go",
                "actions": {"play": action, "go": action, "add": action, "add-hold": action},
            }
            if style:
                row["style"] = style
            return row

        item_loop = [_row("Remove from playlist", ["playlist", "delete", str(playlist_index)])]
        if playlist_index not in (current_index, current_index + 1):
            item_loop.append(_row("Play Next", ["playlist", "move", str(playlist_index)]))
        if not is_current_and_playing:
            item_loop.append(
                _row("Play", ["playlist", "jump", str(playlist_index)], style="itemplay"))

        print(f"[BL] contextmenu: queue menu for player_id={player_id!r} "
              f"playlist_index={playlist_index!r} current_index={current_index!r} "
              f"rows={[row['text'] for row in item_loop]!r}", flush=True)
        result = {
            "count": len(item_loop),
            "offset": 0,
            # windowStyle: "text_list", NOT isContextMenu:1 - confirmed
            # via a real pcap capture of this exact real response (LMS
            # 9.1.1, long-pressing a queue row): {"count":11,"window":
            # {"windowStyle":"text_list"},"item_loop":[...]}. Confirmed
            # via a real device report this was the actual bug even
            # after every other part of this feature started working
            # correctly (the contextmenu request reached this handler,
            # got real non-empty content back - nothing appeared on
            # screen anyway). isContextMenu:1 IS real, confirmed in the
            # same capture, but belongs on the ACTION that navigates
            # here (base.actions.more's own "window" field - already
            # correct, untouched, see aioslimproto's real built-in
            # _handle_status), not on the response data itself.
            "window": {"windowStyle": "text_list"},
            "item_loop": item_loop,
        }
        # DIAG (temporary, not a functional change): prove this exact
        # dict actually round-trips through json.dumps/json.loads before
        # it ever leaves this function - directly rules out (or catches)
        # a serialization problem on our own side, rather than only
        # inferring it from aioslimproto's downstream behavior. TO BACK
        # OUT: delete this whole block, restore the plain "return {...}"
        # above it.
        import json as _diag_json
        try:
            _diag_bytes = _diag_json.dumps(result).encode("utf8")
            print(f"[BL] contextmenu: DIAG - result serializes fine, "
                  f"{len(_diag_bytes)} bytes: {_diag_bytes[:300]!r}", flush=True)
        except Exception as diag_err:  # noqa: BLE001
            print(f"[BL] contextmenu: DIAG - result FAILED to serialize: "
                  f"{diag_err!r}", flush=True)
        return result

    async def _handle_playlist(self, slim_command):
        """Handles the real "playlist" command's jump/delete/move/clear
        subcommands - what the contextmenu rows above (and the "Clear
        Playlist" row in _handle_queue_status below) actually send when
        selected. aioslimproto's own built-in _handle_playlist (cli.py)
        only implements "index +1" and raises NotImplementedError for
        everything else (confirmed by reading its real source before
        writing this) - none of these four are handled anywhere without
        this override.

        Each real MA method confirmed against the actual
        PlayerQueuesController source before use, same discipline as
        every other MA API call in this file:
          - jump: play_index(queue_id, index) - real signature confirmed
            (index: int | str, seek_position=0, fade_in=False - only
            index passed here).
          - delete: delete_item(queue_id, item_id_or_index) - takes a
            raw index directly (confirmed in its own real docstring:
            "Delete item (by id or index)"), no lookup needed.
          - move: move_item(queue_id, queue_item_id, pos_shift=0) - unlike
            delete_item, this one wants a real queue_item_id, not a bare
            index, so the target row's queue_item_id is looked up first
            via items(limit=1, offset=index). pos_shift=0 is a real,
            specifically-documented case (move_item's own real
            docstring): "move item to the front of the upcoming items" -
            exactly what "Play Next" means, and it already handles the
            real buffer-boundary logic (currently-playing/already-
            buffered items) internally, so no target index is computed
            here - see the real contextmenu action this responds to
            (_handle_contextmenu above), which sends just the index, no
            second argument, for the same reason.
          - clear: clear(queue_id) - already used elsewhere in this file
            (see MY_MUSIC_NODE-adjacent code); skip_stop left at its
            real default (False), matching real LMS's own "playlist
            clear" (which does stop playback - confirmed via Slim/Player/
            Playlist.pm's own real source).

        Every branch pushes a real, immediate queue-view update
        afterward via _push_queue_update() (see its own docstring) - the
        same real mechanism playlistcontrol add/insert already use, so
        the queue-view screen reflects a remove/move/clear/jump without
        waiting for the next periodic replay.

        NOT yet verified against a real device test.
        """
        args = slim_command.args
        player_id = slim_command.player_id
        subcommand = args[0] if args else None
        queue = self.mass.player_queues.get_active_queue(player_id)
        if queue is None:
            raise NotImplementedError

        if subcommand in ("jump", "index"):
            # Real LMS treats these as the same underlying jump-and-play
            # action, just reached from different real contexts:
            # "jump" from an explicit menu action (confirmed via the
            # real pcap capture behind _handle_contextmenu above -
            # "Play" sends playlist jump <idx>), "index" from JiveLite's
            # own implicit single-tap-to-play on a style:itemplay queue
            # row - confirmed via a real device log showing
            # ['playlist', 'index', 2] after v53 stripped all per-row
            # actions to match the real confirmed shape (real queue rows
            # carry none at all - see _build_queue_item_loop above).
            # Neither aioslimproto's own built-in (only "index +1", a
            # relative skip-forward, not an absolute jump) nor this
            # handler covered plain "index <N>" before - a real,
            # previously-unnoticed regression: single-tap-to-play on a
            # queue row was silently broken from the moment row actions
            # were removed, since nothing was left to fall back to.
            index = int(args[1])
            print(f"[BL] playlist: {subcommand} index={index!r} on player_id={player_id!r}",
                  flush=True)
            await self._maybe_await(
                self.mass.player_queues.play_index(queue.queue_id, index))
        elif subcommand == "delete":
            index = int(args[1])
            print(f"[BL] playlist: delete index={index!r} on player_id={player_id!r}", flush=True)
            await self._maybe_await(
                self.mass.player_queues.delete_item(queue.queue_id, index))
        elif subcommand == "move":
            # pos_shift=0 is a real, specifically-documented case (not a
            # guess): "move item to the front of the upcoming items"
            # (PlayerQueuesController.move_item's own real docstring) -
            # exactly what "Play Next" means, and it already handles the
            # real buffer-boundary logic (currently-playing/already-
            # buffered items) internally. Deliberately NOT computing a
            # target index ourselves and passing a nonzero pos_shift -
            # that would duplicate (and could drift from) that same
            # boundary handling, and move_item silently no-ops if the
            # computed target ever landed outside the valid range.
            index = int(args[1])
            items = self.mass.player_queues.items(queue.queue_id, limit=1, offset=index)
            if not items:
                raise NotImplementedError
            print(f"[BL] playlist: move index={index!r} (to front of upcoming) "
                  f"on player_id={player_id!r}", flush=True)
            await self._maybe_await(self.mass.player_queues.move_item(
                queue.queue_id, items[0].queue_item_id, pos_shift=0))
        elif subcommand == "clear":
            print(f"[BL] playlist: clear on player_id={player_id!r}", flush=True)
            await self._maybe_await(self.mass.player_queues.clear(queue.queue_id))
        else:
            raise NotImplementedError(f"No handler for playlist/{subcommand}")

        await self._push_queue_update(player_id)
        return None

    @staticmethod
    async def _maybe_await(value):
        """Await value only if it's actually awaitable.

        Added after a real, confirmed bug: a live traceback showed
        "await self.mass.player_queues.delete_item(...)" raising
        "TypeError: 'NoneType' object can't be awaited" - the real
        current upstream source (controllers/player_queues/
        controller.py) confirms delete_item is a plain "def", not
        "async def", returning None directly (including, but not only,
        on its early-return "already loaded in buffer" guard branch -
        that branch is just where this test happened to land first).
        The same reference source shows move_item and clear as equally
        plain "def" - but clear() was independently confirmed working
        via a real device test before this fix existed, which a plain
        sync function wrapped in "await" could not do (awaiting a bare
        None unconditionally raises the same TypeError, every time, no
        exceptions) - a real contradiction, meaning the exact deployed
        MA version may not exactly match the reference source just
        checked. Rather than guess which of the two is right per
        method, this handles both correctly regardless: play_index,
        delete_item, move_item, and clear are all routed through this
        helper now, not just the one confirmed broken.
        """
        if inspect.isawaitable(value):
            await value

    async def _handle_queue_status(self, slim_command):
        """
        Overrides aioslimproto's built-in _handle_status - as of v33, for
        EVERY status call, not just the dedicated queue-view screen.
        Real docker log captures distinguish the two request shapes:
        menu='menu' (args=['-', 10] kwargs={'menu': 'menu',
        'useContextMenu': 1, 'subscribe': 600}) is the dedicated
        queue/Now-Playing-screen request; plain polling status
        (args=['-', 1] kwargs={'tags': ..., 'alarmData': 1}, no 'menu'
        key, fires every few seconds) is the routine heartbeat. Both are
        handled here, but see the cheap-vs-expensive split noted below -
        the polling variant only gets a lightweight two-field patch, not
        the full per-item queue rebuild.

        Root cause this fixes (confirmed by reading _handle_status's own
        source before writing this, not guessed at): the built-in only
        ever reports player.current_media/next_media - two fixed slots,
        not a real queue - so no matter how many tracks MA's queue
        actually holds, or what offset/limit the device asks for (both
        accepted as parameters, neither actually used to slice anything),
        the response is always exactly those same two items. That's the
        real explanation for "queue only ever shows 2, sliding forward as
        each track finishes" - the queue itself was always growing
        correctly; the status response just never looked past current +
        next.

        Sources the real queue via mass.player_queues.get_active_queue()
        (rather than assuming queue_id == player_id - a synced/grouped
        player's own queue may not be its own, confirmed this method
        exists for exactly that reason) and mass.player_queues.items(),
        a real method with genuine limit/offset params (unlike the
        built-in's unused ones). Each row is built via aioslimproto's own
        menu_item_from_media_details(..., include_actions=True) - reused,
        not reinvented, so play/add/add-hold row actions here match real
        LMS-shaped rows exactly. Each real QueueItem's OWN fields (.name,
        .duration, .media_item) feed that row-builder directly, as of
        v35 - see v35's own note below for why mass.player_queues.
        player_media_from_queue_item() (used in v29-v34) was dropped
        entirely rather than kept as a data source.

        BASIC FIRST PASS, same scope caveat as _handle_playlistcontrol - one
        specific thing not yet verified against a real device test:
          - offset='-' is treated as "start at the current queue item"
            (queue.current_index, falling back to 0) - the natural
            reading for a just-opened queue screen, but not checked
            against a real LMS capture of the same request, and there's
            no prior scaffold data point for this the way most of this
            file has. If scrolling further in the real queue view doesn't
            advance correctly, this is the assumption to revisit first.

        get_active_queue()/.items() are NOT awaited (confirmed via a real
        v30 traceback: TypeError: 'PlayerQueue' object can't be awaited).
        HISTORICAL, no longer applicable as of v35: .player_media_from_
        queue_item() WAS awaited (v31's fix, confirmed via a real
        traceback of its own) - that method is no longer called here at
        all, see v35's note, so its async-ness is moot now. Left in the
        history for the general lesson: same-controller/similar-shape
        does not predict async-ness, and now, separately, does not
        predict fitness for a read-only listing either - worth
        remembering both next time a new controller method gets used
        here for the first time.

        v29-v31 all built the RETURNED RESULT entirely from scratch - a
        bare {"count", "offset", "item_loop"} dict - which stopped
        crashing as of v31 but still left the Now Playing screen broken
        (confirmed via a real device test: song plays fine, screen
        doesn't render). Root cause: the built-in's real _handle_status
        computes a lot more than item_loop - player_name, mode, power,
        alarm_state/alarm_snooze_seconds/alarm_timeout_seconds,
        base.actions.more (for this screen's own "more" button), and
        preset_loop/preset_data - all silently dropped by returning a
        from-scratch dict instead. Fixed by calling the real built-in
        first (self.provider.slimproto.cli._handle_status - the same
        real CLI instance provider.py already reaches into directly for
        _handle_jsonrpc_client, confirmed that reference exists before
        using it) with the exact same args/kwargs split aioslimproto's
        own dispatcher already uses (confirmed by matching this file's
        own real log captures: args=['-', 10] positionally as
        offset/limit, kwargs holding menu/useContextMenu/subscribe) - so
        everything the built-in gets right stays right - and only
        overwriting item_loop/count/offset with the real queue-derived
        values afterward, rather than reconstructing the whole response.

        v33 broadened this from menu='menu' only to EVERY status call -
        the plain, frequent polling status (args=['-', 1], no menu key)
        also has its own version of the same 2-item-cap bug:
        "playlist_tracks": len(playlist_items) and a hardcoded
        "playlist_cur_index": 0 in the built-in's source, which is what
        actually drives the Now Playing screen's own "Playing X of Y"
        header - confirmed via a real device report that this always
        read "1 of 2" no matter how large the real queue was, even
        though the dedicated queue-view screen (the menu='menu' variant)
        was by then showing the real count correctly. Since this variant
        fires far more often than the menu one, it only gets the cheap
        patch (playlist_tracks/playlist_cur_index from
        get_active_queue() - already a sync, lightweight call) - NOT the
        expensive per-item queue fetch/conversion the menu variant does,
        which is skipped entirely here.
        """
        kwargs = slim_command.kwargs
        args = slim_command.args
        player_id = slim_command.player_id

        cli = self.provider.slimproto.cli
        result = await cli._handle_status(player_id, *args, **kwargs)
        if result is None:
            raise NotImplementedError  # unknown player - let the built-in reject it

        queue = self.mass.player_queues.get_active_queue(player_id)
        if queue is None:
            # no active queue - the built-in's own (2-item-capped, but at
            # least fully-formed) result stands untouched. Printed as of
            # v37 (previously silent) - a real device test reported the
            # queue reading 0 right after a reboot, and even after an
            # add; this print exists to confirm or rule out whether
            # get_active_queue() is the one returning None at that
            # moment, for either the plain polling status or the
            # menu='menu' queue-view call.
            print(f"[BL] queue status: player_id={player_id!r} menu={kwargs.get('menu')!r} "
                  f"- get_active_queue() returned None, built-in result stands untouched",
                  flush=True)
            return result

        # Cheap fields, patched on EVERY status call (not just menu='menu')
        # - this is what fixes the Now Playing screen's own "Playing X of
        # Y" header, not just the dedicated queue-view screen.
        # playlist_tracks/playlist_cur_index only exist in the built-in's
        # result when player.powered (see _handle_status's own source) -
        # only patch them if the built-in actually included them, so an
        # unpowered player's response shape is untouched.
        if "playlist_tracks" in result:
            result["playlist_tracks"] = queue.items
            result["playlist_cur_index"] = queue.current_index or 0

        if kwargs.get("menu") != "menu":
            # Plain polling status (the frequent "-", 1 heartbeat) -
            # previously silent (only the menu='menu' path printed
            # anything); added as of v37 so the routine heartbeat - the
            # thing actually driving the compact Now Playing header, as
            # opposed to the dedicated queue-view screen - is visible
            # too, since a real device test couldn't otherwise tell
            # which of the two paths a reported "0" was coming from.
            print(f"[BL] queue status (plain): player_id={player_id!r} "
                  f"real_total={queue.items!r} playlist_cur_index={result.get('playlist_cur_index')!r} "
                  f"patched={'playlist_tracks' in result!r}",
                  flush=True)
            return result

        raw_offset = args[0] if len(args) > 0 else "-"
        limit = int(args[1]) if len(args) > 1 else 10
        offset = queue.current_index or 0 if raw_offset == "-" else int(raw_offset)

        item_loop = await self._build_queue_item_loop(queue, offset, limit)

        # "Clear Playlist" - a real extra row real LMS appends after the
        # LAST real track (confirmed via Slim/Control/Queries.pm's own
        # _addJivePlaylistControls()/statusQuery(): "add clear and save
        # playlist items at the bottom" when $idx+1 == $songCount, and
        # "count" += 2 for the non-empty case - here just +1, since real
        # LMS's own matching "Save Playlist" row isn't built here.
        # real LMS's own real shape: an outer "Clear Playlist" row whose
        # OWN item_loop holds two real, INLINE (no extra round-trip)
        # rows - "Cancel" (go, cmd: ['jiveblankcommand'], real LMS's own
        # client-side no-op - see _dispatch above) and "Clear Playlist"
        # again as the real confirm (do, cmd: ['playlist', 'clear']).
        # icon uses this project's own established local-path convention
        # ("icon": "html/images/X.png", resolved server-side by
        # _resolve_static_icon_path below) rather than real LMS's own
        # "icon-id" key for this specific row - proven correct here for
        # exactly this kind of static chrome asset (MY_MUSIC_NODE tiles),
        # unlike real LMS's own convention which this project doesn't
        # otherwise follow. Needs a real playlistclear_225x225_m.png (or
        # playlistclear.png fallback) placed in static/ - not included
        # here, same as every other chrome icon in this file.
        reaches_tail = offset + len(item_loop) >= queue.items
        if reaches_tail and queue.items > 0:
            item_loop = [
                *item_loop,
                {
                    # No "type" key here - confirmed via a direct
                    # re-check of the real source (_addJivePlaylistControls,
                    # Slim/Control/Queries.pm): it sets text/icon-id/
                    # offset/count/item_loop on this row and nothing
                    # else. Earlier version of this added "type":
                    # "playlist" without that being in the real source -
                    # an unverified guess, and confirmed via a real
                    # device test to be the actual bug: pressing this row
                    # went straight to a blank screen with ZERO trace of
                    # any request ever reaching the server (no
                    # [BL] print at all, while ordinary heartbeat traffic
                    # kept flowing normally through the same moment) -
                    # meaning the failure was purely client-side
                    # rendering, before JiveLite ever got far enough to
                    # send anything. Removed to match the real shape
                    # exactly rather than guess again.
                    "text": "Clear Playlist",
                    "icon": "html/images/playlistclear.png",
                    "count": 2,
                    "offset": 0,
                    "item_loop": [
                        {
                            "text": "Cancel",
                            "actions": {"go": {"player": 0, "cmd": ["jiveblankcommand"]}},
                            "nextWindow": "parent",
                        },
                        {
                            "text": "Clear Playlist",
                            "actions": {"do": {"player": 0, "cmd": ["playlist", "clear"]}},
                            "nextWindow": "home",
                        },
                    ],
                },
            ]

        print(f"[BL] queue status: player_id={player_id!r} offset={offset!r} "
              f"limit={limit!r} real_total={queue.items!r} returned={len(item_loop)!r} "
              f"titles={[row.get('track') for row in item_loop]!r}",
              flush=True)

        # Patch just the truncated fields - everything else the built-in
        # already computed (player_name, mode, power, alarm data,
        # base.actions.more, preset_loop/preset_data, ...) stays exactly
        # as the real built-in returned it.
        result["item_loop"] = item_loop
        result["count"] = queue.items + (1 if queue.items > 0 else 0)
        result["offset"] = offset
        # v52 added base["addAction"] = "more" here, reasoning from
        # SlimBrowserApplet.lua's _actionHandler source (real, but only
        # part of the picture). A real pcap capture of this exact real
        # base.actions.more object (LMS 9.1.1) shows no "addAction" key
        # at all - removed to match. The real, pcap-confirmed fix turned
        # out to be simpler: real queue rows carry NO "actions" field of
        # their own at all (see _build_queue_item_loop above) - with
        # nothing at the row level left to shadow this base action,
        # there's nothing for an addAction flag to route around either.
        return result

    async def _build_queue_item_loop(self, queue, offset, limit):
        """Build a real item_loop (SlimMenuItem rows) for a slice of a
        real MA queue - factored out of _handle_queue_status as of v38
        so the new post-add/insert "replace" list push (see
        _handle_playlistcontrol) can build the exact same real rows
        without duplicating this logic a second time.

        Real fields read directly from QueueItem - NOT
        player_media_from_queue_item(). Confirmed via a real traceback
        that this was the actual cause of newly-added tracks "not
        showing" for minutes at a time: it raises
        InvalidDataError("Queue session_id is None") for any item that
        isn't already part of an active streaming session - which is
        only ever current_media/next_media at most, the same 2-item
        universe the original built-in was stuck in. Every request
        touching a queue item beyond that was failing outright (caught
        by __call__, logged, re-raised), not returning stale data - so
        nothing "caught up" on its own until playback advanced far
        enough for MA to assign that item a session as part of its
        normal preload behavior. QueueItem itself already carries
        everything a listing row needs without requiring a session:
        .name (title), .duration, .media_item (the real underlying
        Track/etc.) - confirmed via real signature introspection before
        using any of these, not guessed.
        """
        queue_items = self.mass.player_queues.items(
            queue.queue_id, limit=limit, offset=offset)

        item_loop = []
        for i, queue_item in enumerate(queue_items):
            track = queue_item.media_item
            artist = (getattr(track, "artist_str", "") or "") if track else ""
            album_obj = getattr(track, "album", None) if track else None
            album = getattr(album_obj, "name", "") or ""
            uri = (getattr(track, "uri", None) or "") if track else ""
            # v40/v41 both tried to eagerly resolve a real image_url
            # here (via _pick_best_image + get_image_url/
            # get_image_url_for_item) and hand that directly to the
            # device. Root cause, confirmed via a real capture: when
            # the best-ranked image is remotely-hosted (which
            # _pick_best_image deliberately prefers - TheAudioDB,
            # fanart.tv, etc.), the resulting value ends up as a raw
            # external URL, and the device/aioslimproto wraps that in a
            # local "/imageproxy/<url>/image_WxH_m" request expecting
            # THIS server to fetch and proxy it - a route this server
            # has never implemented, confirmed via a real [ICON]
            # UNMATCHED capture of exactly that request shape,
            # coinciding with a track (New Truck) whose album art is
            # otherwise proven to serve correctly via the real
            # /music/{id}/cover route in the very same capture.
            #
            # Real fix: don't resolve a URL here at all - just point at
            # that same already-working local route, exactly the same
            # f"music/{id}/cover" format every other real icon in this
            # file already uses (get_albums/get_playlists/get_radio_
            # stations/etc., all confirmed serving real art correctly).
            # handle_icon (below) already does the real resolution -
            # _pick_best_image, remote-image proxying included - lazily
            # and server-side when the device actually requests it, the
            # same as it already does for every other icon in this
            # file; there's no reason queue rows needed a second,
            # eager, URL-based resolution path of their own, and that
            # second path is exactly what broke.
            if album_obj is not None and getattr(album_obj, "item_id", None) is not None:
                icon_path = f"music/{album_obj.item_id}/cover"
            else:
                # No album to key off - a genuine edge case (e.g. a
                # queue item with no library album at all). Confirmed
                # via a real report that this file must NEVER hand a
                # resolved URL (raw or proxy-shaped) to the client
                # directly, in any code path - the client should only
                # ever see local music/{id}/cover-style identifiers and
                # let this server resolve them server-side on request,
                # the same as every other real icon here. There's no
                # local route this project has for a bare track id
                # (_fetch_real_item_art's own icon_id scheme treats any
                # unprefixed numeric id as an ALBUM, not a track - see
                # its own docstring), so there's nothing local to point
                # at here; leaving the icon blank (client shows no
                # image/a placeholder) is the correct, safe fallback -
                # not calling get_image_url_for_item() and leaking
                # whatever it returns, which is exactly the mistake
                # that broke queue rows in the first place.
                icon_path = ""
            media_details = MediaDetails(
                url=uri,
                metadata={
                    # Real bare numeric ID (e.g. 26566), NOT the full
                    # "library://track/N" uri - confirmed via a direct,
                    # side-by-side comparison of a real LMS client trace
                    # against our own for the exact same interaction
                    # (long-press a queue row): real LMS's own outgoing
                    # contextmenu request carries track_id:26566 (bare
                    # numeric); ours carried track_id:library://track/25
                    # (full uri) - the one concrete difference between a
                    # request that correctly reached _browseSink()/
                    # "Pushed" on real LMS, and one that instead hit
                    # "Hiding NP child window"/"Popped" on this server,
                    # every other time this was tested. track.item_id is
                    # the real bare numeric field (confirmed via real
                    # signature introspection, same as everything else
                    # this loop reads off track) - falling back to uri
                    # only if a queue item genuinely has no item_id.
                    "item_id": getattr(track, "item_id", None) or uri,
                    "title": queue_item.name,
                    "album": album,
                    "artist": artist,
                    "image_url": icon_path or "",
                    "duration": queue_item.duration or 0,
                },
            )
            row = menu_item_from_media_details(media_details, include_actions=False)
            # Real, pcap-confirmed row shape (a real LMS 9.1.1 queue-view
            # capture, not inferred): a queue row carries NO "actions"
            # field at all - not go, not add, not more, nothing - just
            # style/text/track/album/artist/params/icon. Single-tap-to-
            # play still works against real LMS with zero actions on the
            # row, confirming JiveLite's own tap handling for
            # style:itemplay rows doesn't need an explicit "go" action at
            # all here - it must derive play-this-index behavior from
            # context (params.playlist_index) rather than a literal
            # action lookup. v51's row["actions"].pop("more", None) was
            # a real, reasonable attempt at the time but the wrong fix -
            # the actual conflict was "add" (what a touchscreen hold
            # resolves to, per real JiveLite source - Menu.lua's real
            # EVENT_MOUSE_HOLD unconditionally does Framework:pushAction
            # ("add")), not "more", and the real, confirmed fix is
            # simpler than picking which action(s) to remove: match
            # real LMS exactly and don't build a per-row actions dict at
            # all. include_actions=False skips building it in the first
            # place, rather than building then stripping.
            row["style"] = "itemplay"
            row.pop("type", None)
            # menu_item_from_media_details() (aioslimproto's own
            # function) sets details["nextWindow"] = "nowPlaying"
            # UNCONDITIONALLY at its very end - outside its own
            # include_actions check, confirmed by reading its real
            # source directly (cli.py) - so it survived even after v53
            # switched to include_actions=False. Real LMS's own queue
            # rows never carry a row-level nextWindow at all (confirmed
            # via multiple real captures). Popped here because a direct,
            # line-by-line comparison of a failing vs a working client
            # trace for the identical long-press interaction showed the
            # working (real LMS) trace logging "Context Menu" /
            # "_newWindowSpec()" / "_newDestination():" right after
            # "_actionHandler(more): json action", while the failing
            # (this server's) trace skipped straight from that same line
            # to "_performJSONAction(from:nil, qty:nil)" - the entire
            # Context Menu branch never ran. That skip is consistent
            # with the client falling back to a row's own nextWindow
            # value regardless of which action actually fired,
            # explaining the observed "Hiding NP child window" ->
            # _goNowPlaying() behavior exactly.
            row.pop("nextWindow", None)
            # params: real capture shows ONLY track_id + playlist_index -
            # no item_id/uri. Matched exactly here too, not just because
            # it's unused elsewhere, but because it's what's actually
            # proven to work.
            row["params"] = {
                "track_id": row["params"].get("track_id"),
                "playlist_index": offset + i,
            }
            item_loop.append(row)
        return item_loop


# ---------------------------------------------------------------------------
# Icon / cover art serving
#
# ARCHITECTURAL RULE, decided in a prior session, re-confirmed after v40/v41
# accidentally violated it: the client is NEVER handed a real or proxy image
# URL directly - not MA's own remote URLs, not MA's own /imageproxy/ paths,
# nothing resolved-looking at all. Every "icon"/image_url this project ever
# gives the client is a local identifier in this server's own namespace
# (f"music/{id}/cover", "artist-{id}", chrome icon filenames, etc.) that
# THIS code resolves server-side, on request, via _fetch_real_item_art
# below. This is deliberate, not incidental: keeping this server in the
# middle of every real image fetch is what allows it to make its own
# adjustments later - e.g. serving a smaller/differently-encoded icon to a
# known low-power device - flexibility that's lost the moment a client is
# handed a URL it can resolve on its own without this code ever seeing the
# request again. v40/v41 broke this for queue-view rows specifically (eager
# URL resolution baked directly into item_loop's image_url field), which
# is what actually caused the "some queue rows have no art" bug those two
# versions were chasing - not a resolution-quality problem at all, a
# never-expose-a-resolved-URL rule that got violated without anyone
# involved at the time realizing it was already a settled decision. v42/v43
# fixed it back to local identifiers only; if a future change is tempted to
# resolve+embed a URL again for convenience, this is why not to.
#
# Real MA album art, for the one icon route that already carries a real
# item_id (get_albums' "icon": f"music/{album.item_id}/cover" - see above).
# Falls back to the same solid-color placeholder as before on any failure
# (album not found, no image, image fetch error) - a missing/broken piece of
# art should degrade to a placeholder square, never a broken image or a 404.
#
# Verified against the real music_assistant.controllers.metadata source
# (ImageProxyMixin in controllers/metadata/images.py), not guessed:
#   - mass.metadata.get_image_url_for_item(media_item) resolves a MediaItem
#     (Album, Track, Artist, ...) to a fetchable URL - handling local-file
#     vs. remote vs. provider-specific resolution, and MA's own built-in
#     "no image on this item? try its artist/album instead" fallback chain,
#     entirely internally.
#   - mass.metadata.get_thumbnail(path, provider="builtin", size=..., ...)
#     then fetches/resizes/caches actual bytes from that URL. provider=
#     "builtin" is the exact pattern MA's own get_image_data_for_item uses
#     for this same two-step call - the resolution step above has already
#     turned a provider-specific reference into something directly
#     fetchable, so the second step no longer needs the original provider.
#   - image_format="jpeg", flatten_transparency=True mirrors the comment in
#     images.py's own _serve_thumbnail: "players are sent a JPEG for maximum
#     compatibility... since JPEG has no alpha channel we composite
#     transparency onto white" - the same treatment real playback art
#     already gets, which is exactly the JiveLite-facing scenario here too.
#
# Chrome icons (/html/images/*.png - the three distinct per-tile names on
# MY_MUSIC_NODE: AlbumArtists/AllArtists/Albums) are served from real files
# in static/ (see STATIC_DIR below) - these are actual icons downloaded
# from a real LMS server (same asset LMS itself serves, saved to disk once
# rather than fetched from a live LMS on every request), not generated.
# The solid-color placeholder is now only a last-resort fallback if a
# specific file genuinely isn't found in static/ (chrome icon) or nothing
# resolves via _fetch_real_item_art (album/artist cover art), same
# fail-safe role it plays everywhere else in this project - never a broken
# image or a 404 for something we could reasonably render instead.
#
# Real per-item art now covers both albums and artists (see get_albums()/
# get_artists() above and _fetch_real_item_art below for the icon_id
# namespacing that keeps the two from colliding) - reached either via the
# proper wrapped path or, as before, the bare-icon_id fallback in
# handle_unmatched below.
# ---------------------------------------------------------------------------

_PNG_CACHE = {}

# Real chrome icon files, downloaded from a live LMS server the same way
# the standalone scaffold's download_static_assets.py did - not generated,
# and not re-fetched at runtime. Lives next to this file so it travels with
# the package regardless of where it's installed (Path(__file__).parent
# resolves correctly either way) - same STATIC_DIR pattern the scaffold
# used. reinject.sh needs to copy this whole directory alongside the four
# .py files it already copies; see that script's own comment at the
# docker cp line for this.
STATIC_DIR = (Path(__file__).parent / "static").resolve()

# Ported from the standalone scaffold's icon_handler (same regex, same
# role): real JiveLite requests a size suffix on every chrome icon path
# (e.g. 'AlbumArtists_225x225_m.png') - this splits that back into the
# base name, the suffix, and the extension so _resolve_static_icon_path
# can look for a real on-disk file matching just the base name.
_STATIC_ICON_SUFFIX_RE = re.compile(
    r"^(?P<base>.+?)_(?P<w>\d+)x(?P<h>\d+)_(?P<mode>[a-zA-Z])(?P<ext>\.[a-zA-Z0-9]+)?$"
)


def _resolve_static_icon_path(filename):
    """Resolve a requested chrome-icon filename to a real Path in
    static/, or None if nothing on disk matches (caller falls back to the
    placeholder). 1:1 by design - the requested base name (e.g.
    "AlbumArtists" out of "AlbumArtists_225x225_m.png") has to match a real
    file's base name exactly; no aliasing or indirection. Each of
    MY_MUSIC_NODE's per-menu-item icon names above (AlbumArtists.png,
    AllArtists.png, Albums.png) needs its own real file in static/ - even
    where two of them look the same today, they're independent images,
    kept in sync (or not) by whoever maintains static/, not by this code.

    Tries the exact size-suffixed filename first (e.g.
    'AlbumArtists_225x225_m.png'), then falls back to the plain
    suffix-stripped name (e.g. 'AlbumArtists.png'), in case only an
    unsized master image exists for a given asset - same two-candidate
    fallback order the scaffold used, for the same reason (a real device
    always sends a specific requested size, but we may only have one
    fixed image to offer regardless of which size was asked for).
    """
    m = _STATIC_ICON_SUFFIX_RE.match(filename)
    if m:
        base = m.group("base")
        ext = m.group("ext") or ".png"
        suffix = f"_{m.group('w')}x{m.group('h')}_{m.group('mode')}"
    else:
        base, ext, suffix = filename, (Path(filename).suffix or ".png"), ""
    for candidate_name in (f"{base}{suffix}{ext}", f"{base}{ext}"):
        candidate = (STATIC_DIR / candidate_name).resolve()
        if candidate.is_file() and STATIC_DIR in candidate.parents:
            return candidate
    return None

# Matches the size suffix real JiveLite puts on every image path (e.g.
# '.../cover_225x225_m') - see this section's own history for why we only
# used to need this for logging, and now need it to ask MA for a
# sensibly-sized thumbnail instead of the full-resolution source image.
_COVER_SIZE_RE = re.compile(r"_(?P<w>\d+)x(?P<h>\d+)_[a-zA-Z]$")

# Used only when a request carries no size suffix - real JiveLite always
# sends one for cover art (see _COVER_SIZE_RE above), so this mainly matters
# for the bare-icon_id fallback path in handle_unmatched, which never
# carries one. Not tied to MA's own imageproxy's allowed-sizes allowlist
# (an HTTP-route-only input validation, not enforced on the get_thumbnail()
# call we use directly) - just a reasonable default for an embedded display.
_DEFAULT_COVER_SIZE = 300


def _requested_cover_size(path):
    """Extract the requested pixel size from a JiveLite icon path suffix,
    e.g. '.../cover_225x225_m' -> 225 (the larger of width/height, in case
    they ever differ)."""
    if m := _COVER_SIZE_RE.search(path):
        return max(int(m.group("w")), int(m.group("h")))
    return _DEFAULT_COVER_SIZE


def _artist_no_art_response(request_path):
    """Real LMS's own behavior when an artist has no photo: fall back to
    the generic Artists chrome icon (AllArtists_*.png - see
    _resolve_static_icon_path above) rather than a blank/placeholder
    image. Reuses whichever size suffix the client actually requested
    (falling back to 225, since that's the one size we actually have a
    real AllArtists file for - see static/'s own contents). Returns a
    Response, or None if that file isn't available either, in which case
    the caller falls through to the solid-color placeholder exactly as
    before - this is strictly an extra rung on the fallback ladder, not a
    replacement for it.

    Was "AlbumArtists" here - switched to "AllArtists" alongside the
    Artists/Tracks menu restructure (see MY_MUSIC_NODE's own notes),
    which fully retired the "Album Artists" concept; this project no
    longer needs an AlbumArtists.png file to exist at all.
    """
    m = _COVER_SIZE_RE.search(request_path)
    suffix = f"_{m.group('w')}x{m.group('h')}_m" if m else "_225x225_m"
    static_path = _resolve_static_icon_path(f"AllArtists{suffix}.png")
    if static_path is None:
        return None
    content_type = "image/png" if static_path.suffix.lower() == ".png" else "image/jpeg"
    return web.Response(
        body=static_path.read_bytes(), content_type=content_type,
        headers={"Cache-Control": "max-age=86400"},
    )


# Extensions we treat as "this local image is actually embedded inside an
# audio file's own tags, not a real standalone cover file" - used by
# _pick_best_image below to rank those last. Not exhaustive of every format
# MA's providers might report, but covers the common ones; worth extending
# if a real capture ever shows something else showing up here.
_AUDIO_EXTENSIONS = (".mp3", ".flac", ".m4a", ".ogg", ".oga", ".wav",
                     ".aac", ".wma", ".alac", ".aiff", ".opus")


def _pick_best_image(images, img_type):
    """Pick the best candidate of img_type (e.g. ImageType.THUMB) from a
    MediaItem's own images list, in this priority order:
      1. any remotely_accessible image (internet-hosted - TheAudioDB,
         fanart.tv, etc.) - generally something someone deliberately
         curated/uploaded, and consistently the highest-quality option in
         every real album checked so far (see this function's history).
      2. a local standalone image file (e.g. 'Folder.jpg') - a real cover
         file someone placed in the album folder.
      3. a local image embedded inside an audio file itself (path ends in
         a known audio extension, see _AUDIO_EXTENSIONS) - last resort,
         since embedded-in-track art is commonly lower resolution/quality
         than a dedicated cover file, and is genuinely the source of the
         "grainy" covers this was built to fix.
    Deliberately NOT based on any real width/height/quality data - the
    MediaItemImage model doesn't carry any (verified: type, path, provider,
    remotely_accessible, proxy_id are its only fields) - this is a priority
    order over metadata we do have, not a real quality comparison.
    Returns None if nothing of that type exists at all.
    """
    candidates = [img for img in images if img.type == img_type]
    if not candidates:
        return None
    remote = [img for img in candidates if img.remotely_accessible]
    if remote:
        return remote[0]
    standalone = [img for img in candidates if not img.path.lower().endswith(_AUDIO_EXTENSIONS)]
    if standalone:
        return standalone[0]
    return candidates[0]


async def _fetch_real_item_art(mass, icon_id, size):
    """Try to resolve icon_id as a real MA library item and return real
    art bytes, or None if anything along the way doesn't pan out (unknown
    id, item not found, no image available, fetch error). Never raises -
    every failure mode here just means "the caller should fall back to
    the placeholder", not a broken response.

    icon_id is one of six shapes (see get_albums()/get_artists()/
    get_playlists()/get_radio_stations()/get_podcasts()/get_audiobooks()
    above, which are what actually generate these):
      - a bare item_id (e.g. "42") - an Album, MediaControllerBase.
        get_library_item does int(item_id) internally, which is also
        what rejects a non-numeric icon_id here (e.g. a chrome-icon-shaped
        bare path that reached here via handle_unmatched's fallback).
      - "artist-<item_id>" (e.g. "artist-7") - an Artist.
      - "playlist-<item_id>" (e.g. "playlist-3") - a Playlist.
      - "radio-<item_id>" (e.g. "radio-9") - a Radio station.
      - "podcast-<item_id>" (e.g. "podcast-2") - a Podcast.
      - "audiobook-<item_id>" (e.g. "audiobook-5") - an Audiobook.
      Artists, playlists, radio stations, podcasts, and audiobooks are
      namespaced rather than also bare, since albums, artists, playlists,
      radio stations, podcasts, and audiobooks each have their own
      independent id space in MA - an unqualified numeric icon_id would
      be ambiguous between them once all six share this same lookup
      (could silently resolve to an unrelated item that happens to have
      the same numeric id).
    """
    if icon_id.startswith("artist-"):
        controller, real_id = mass.music.artists, icon_id[len("artist-"):]
    elif icon_id.startswith("playlist-"):
        controller, real_id = mass.music.playlists, icon_id[len("playlist-"):]
    elif icon_id.startswith("radio-"):
        controller, real_id = mass.music.radio, icon_id[len("radio-"):]
    elif icon_id.startswith("podcast-"):
        controller, real_id = mass.music.podcasts, icon_id[len("podcast-"):]
    elif icon_id.startswith("audiobook-"):
        controller, real_id = mass.music.audiobooks, icon_id[len("audiobook-"):]
    else:
        controller, real_id = mass.music.albums, icon_id
    try:
        item = await controller.get_library_item(real_id)
    except (MediaNotFoundError, ValueError):
        return None
    # Prefer a real quality ranking over MA's own "first match in the list
    # wins" default (get_image_url_for_item) - confirmed via a real capture
    # that local filesystem images consistently sort first for every album
    # checked, which is exactly the source of the graininess this exists to
    # fix. Falls back to get_image_url_for_item's own chain (Track->album,
    # Album->artist) only when this item has no images of its own at all -
    # that fallback logic is more involved than picking among an existing
    # list, and reusing it here avoids re-implementing it.
    images = getattr(getattr(item, "metadata", None), "images", None) or []
    chosen = _pick_best_image(images, ImageType.THUMB)
    if chosen is not None:
        img_path = mass.metadata.get_image_url(chosen, prefer_proxy=not chosen.remotely_accessible)
    else:
        img_path = await mass.metadata.get_image_url_for_item(item)
    if not img_path:
        return None
    try:
        return await mass.metadata.get_thumbnail(
            img_path, provider="builtin", size=size,
            image_format="jpeg", flatten_transparency=True,
        )
    except MediaNotFoundError:
        return None


def _make_placeholder_png(rgb, size=64):
    """Pure-stdlib solid-color PNG generator (no PIL dependency) - a
    distinct color per icon type so placeholders are at least visually
    distinguishable from each other on a real device's screen."""
    cache_key = (rgb, size)
    if cache_key in _PNG_CACHE:
        return _PNG_CACHE[cache_key]

    def chunk(tag, data):
        c = tag + data
        return struct.pack(">I", len(data)) + c + struct.pack(">I", zlib.crc32(c) & 0xFFFFFFFF)

    sig = b"\x89PNG\r\n\x1a\n"
    ihdr = struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0)  # 8-bit depth, RGB
    row = bytes(rgb) * size
    raw = (b"\x00" + row) * size  # filter type 0 (none) prefix per scanline
    idat = zlib.compress(raw, 9)
    png = sig + chunk(b"IHDR", ihdr) + chunk(b"IDAT", idat) + chunk(b"IEND", b"")
    _PNG_CACHE[cache_key] = png
    return png


# Distinct placeholder colors so artists/albums/cover-art are at least
# visually distinguishable from each other, even as plain squares.
_PLACEHOLDER_COLORS = {
    "artists": (100, 149, 237),  # cornflower blue
    "albums": (60, 179, 113),  # medium sea green
    "cover": (218, 165, 32),  # goldenrod - distinct from the generic chrome icons
}


def _placeholder_response(color_key):
    png = _make_placeholder_png(_PLACEHOLDER_COLORS[color_key])
    return web.Response(
        body=png,
        content_type="image/png",
        headers={"Cache-Control": "no-store"},  # placeholder - don't let real devices cache this long-term
    )


def make_icon_routes(mass):
    """Build the handle_icon/handle_unmatched closures bound to `mass`.

    A factory rather than plain module-level functions (which is what these
    used to be) because real cover art needs mass.music/mass.metadata
    access, and these are registered directly as aiohttp route handlers via
    provider.py's extra_routes - aiohttp calls them with just a `request`,
    so there's no other place to thread mass through except a closure
    captured at registration time. See provider.py's own comment at the
    extra_routes= call site for why it's built this way there too.
    """

    async def handle_icon(request: web.Request) -> web.Response:
        """Serves real cover art for '/music/<icon-id>/cover_<size>'
        (icon-id is a real Album.item_id - see get_albums() above) and
        real chrome icon files from static/ for generic UI icons
        ('/html/images/<name>_<size>.png' - see _resolve_static_icon_path
        above). Registered directly on aioslimproto's own internal webapp
        (see provider.py) since mass.streams.register_dynamic_route only
        supports exact-string paths, not the variable path segments
        (icon-id, size) these need - aiohttp's own router, which
        aioslimproto's webapp is a real instance of, supports this
        natively via {name} route patterns.
        """
        path = request.path
        print(f"[ICON] handle_icon CALLED for path={path!r}", flush=True)
        if "/cover" in path:
            icon_id = request.match_info.get("icon_id")
            size = _requested_cover_size(path)
            real = await _fetch_real_item_art(mass, icon_id, size) if icon_id else None
            if real is not None:
                print(f"[ICON] handle_icon: serving REAL cover art for icon_id={icon_id!r} "
                      f"size={size}", flush=True)
                return web.Response(
                    body=real, content_type="image/jpeg",
                    headers={"Cache-Control": "max-age=86400"},
                )
            print(f"[ICON] handle_icon: no real art for icon_id={icon_id!r} "
                  f"- falling back", flush=True)
            if icon_id and icon_id.startswith("artist-"):
                fallback = _artist_no_art_response(path)
                if fallback is not None:
                    print(f"[ICON] handle_icon: serving generic Artists icon "
                          f"for icon_id={icon_id!r} (no real photo)", flush=True)
                    return fallback
            return _placeholder_response("cover")

        filename = request.match_info.get("filename")
        static_path = _resolve_static_icon_path(filename) if filename else None
        if static_path is not None:
            print(f"[ICON] handle_icon: serving static file {static_path.name} "
                  f"for filename={filename!r}", flush=True)
            content_type = "image/png" if static_path.suffix.lower() == ".png" else "image/jpeg"
            # Cache-Control/Expires mirror a real LMS response for these -
            # chrome icons never change, so a real device is meant to cache
            # them for a year rather than re-fetching every time.
            expires = (datetime.now(timezone.utc) + timedelta(days=365)) \
                .strftime("%a, %d %b %Y %H:%M:%S GMT")
            return web.Response(
                body=static_path.read_bytes(), content_type=content_type,
                headers={"Cache-Control": "max-age=31536000", "Expires": expires},
            )
        print(f"[ICON] handle_icon: no static file for filename={filename!r} "
              f"- falling back to placeholder", flush=True)
        if "albums" in path:
            return _placeholder_response("albums")
        return _placeholder_response("artists")

    async def handle_unmatched(request: web.Request) -> web.Response:
        """Catch-all for any GET request that doesn't match one of our
        specific icon routes (/html/images/{filename}, /music/{icon_id}/{filename}).
        Registered as the lowest-priority route in provider.py, so it only
        fires for genuinely unmatched paths - forced print so we can see
        exactly what a real device requests that we're not currently handling,
        rather than a silent 404 with zero visibility (access logging is
        explicitly disabled on this webapp - see provider.py)."""
        print(f"[ICON] UNMATCHED request: method={request.method} path={request.path!r} "
              f"query={dict(request.query)!r}", flush=True)

        # bare-icon_id fallback - this is the real cause of the missing-album-art
        # bug (confirmed via docker logs: '/album1'..'/album5' with Stage 1's
        # test data, exactly our icon_id values, no wrapper, no size suffix).
        # Not a root-cause fix - see the scaffold's icon_handler docstring for
        # what was already tried and ruled out.
        #
        # Now tries the same real-art lookup handle_icon's cover branch uses
        # (see _fetch_real_item_art above) before falling back to the
        # placeholder - a bare path that isn't a real Album.item_id or
        # "artist-<item_id>" (e.g. a stray chrome-icon-shaped request) just
        # returns None from that helper and falls through to the
        # placeholder exactly as before.
        bare = request.path.lstrip("/")
        if "/" not in bare and bare:
            size = _requested_cover_size(request.path)
            real = await _fetch_real_item_art(mass, bare, size)
            if real is not None:
                print(f"[ICON] bare-icon_id fallback MATCHED path={request.path!r} "
                      f"-> serving REAL cover art for icon_id={bare!r} size={size}", flush=True)
                return web.Response(
                    body=real, content_type="image/jpeg",
                    headers={"Cache-Control": "max-age=86400"},
                )
            print(f"[ICON] bare-icon_id fallback MATCHED path={request.path!r} "
                  f"-> no real art for {bare!r} - falling back", flush=True)
            if bare.startswith("artist-"):
                fallback = _artist_no_art_response(request.path)
                if fallback is not None:
                    print(f"[ICON] bare-icon_id fallback MATCHED path={request.path!r} "
                          f"-> serving generic Artists icon for {bare!r} (no real photo)", flush=True)
                    return fallback
            return _placeholder_response("cover")

        return web.Response(status=404, text="Not Found", content_type="text/plain")

    return handle_icon, handle_unmatched