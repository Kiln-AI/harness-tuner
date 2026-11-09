# Git Sync API

Git-based synchronization setup, configuration, and management.

|Endpoint|Method|Name|Description|API Details URL|
|-|-|-|-|-|
|`/api/git_sync/test_access`|POST|Test Git Remote Access||post_api_git_sync_test_access.md|
|`/api/git_sync/list_branches`|POST|List Remote Branches||post_api_git_sync_list_branches.md|
|`/api/git_sync/clone`|POST|Clone Repository||post_api_git_sync_clone.md|
|`/api/git_sync/test_write_access`|POST|Test Write Access||post_api_git_sync_test_write_access.md|
|`/api/git_sync/scan_projects`|POST|Scan for Kiln Projects||post_api_git_sync_scan_projects.md|
|`/api/git_sync/rename_clone`|POST|Rename Clone to Final Path||post_api_git_sync_rename_clone.md|
|`/api/git_sync/save_config`|POST|Save Git Sync Config||post_api_git_sync_save_config.md|
|`/api/git_sync/config/{project_id}`|GET|Get Git Sync Config||get_api_git_sync_config_project_id.md|
|`/api/git_sync/config/{project_id}`|PATCH|Update Git Sync Config||patch_api_git_sync_config_project_id.md|
|`/api/git_sync/config/{project_id}`|DELETE|Delete Git Sync Config||delete_api_git_sync_config_project_id.md|
|`/api/git_sync/oauth/start`|POST|Start GitHub OAuth Flow||post_api_git_sync_oauth_start.md|
|`/api/git_sync/oauth/callback`|GET|OAuth Callback||get_api_git_sync_oauth_callback.md|
|`/api/git_sync/oauth/authorize`|GET|GitHub App Install Complete||get_api_git_sync_oauth_authorize.md|
|`/api/git_sync/oauth/status/{state}`|GET|OAuth Flow Status||get_api_git_sync_oauth_status_state.md|
