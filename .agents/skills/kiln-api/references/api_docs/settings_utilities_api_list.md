# Settings & Utilities API

Server settings, connectivity checks, and utility endpoints.

|Endpoint|Method|Name|Description|API Details URL|
|-|-|-|-|-|
|`/ping`|GET|Ping Server|Ping the server to check connectivity.|get_ping.md|
|`/api/settings`|GET|Get Settings||get_api_settings.md|
|`/api/settings`|POST|Update Settings||post_api_settings.md|
|`/api/settings/{item_id}`|GET|Get Setting Item||get_api_settings_item_id.md|
|`/api/open_logs`|POST|Open Logs Folder|Opens the log folder in the system file browser.|post_api_open_logs.md|
|`/api/open_project_folder/{project_id}`|POST|Open Project Folder|Opens the project folder in the system file browser.|post_api_open_project_folder_project_id.md|
|`/api/check_entitlements`|GET|Check Entitlements|Check whether the authenticated user has the given entitlements.  The feature_codes parameter should...|get_api_check_entitlements.md|
|`/api/select_kiln_file`|GET|Select Kiln File||get_api_select_kiln_file.md|
