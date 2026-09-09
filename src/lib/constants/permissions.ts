export const DEFAULT_PERMISSIONS = {
	sharing: {
		models: false,
		public_models: false,
		knowledge: false,
		public_knowledge: false,
		prompts: false,
		public_prompts: false,
		tools: false,
		public_tools: false,
		skills: false,
		public_skills: false,
		folders: false,
		public_chats: false,
		open_chats: false
	},
	access_grants: {
		allow_users: true,
		allow_groups: true
	},
	chat: {
		controls: true,
		valves: true,
		system_prompt: true,
		params: true,
		file_upload: true,
		web_upload: true,
		delete: true,
		delete_message: true,
		continue_response: true,
		regenerate_response: true,
		rate_response: true,
		edit: true,
		share: true,
		export: true,
		import: true,
		stt: true,
		tts: true,
		call: true,
		multiple_models: true,
		temporary: true,
		temporary_enforced: false
	},
	features: {
		api_keys: false,
		channels: true,
		folders: true,
		direct_tool_servers: false,
		web_search: true,
		image_generation: true,
		code_interpreter: true,
		memories: true,
		automations: false,
		webhooks: false
	},
	settings: {
		interface: true
	}
} as const;
