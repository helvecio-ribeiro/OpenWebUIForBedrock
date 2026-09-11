<script lang="ts">
	import { toast } from 'svelte-sonner';
	import { getContext, onMount } from 'svelte';
	const i18n = getContext('i18n');

	import Modal from '$lib/components/common/Modal.svelte';
	import SensitiveInput from '$lib/components/common/SensitiveInput.svelte';
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import Switch from '$lib/components/common/Switch.svelte';
	import { verifyToolServerConnection, registerOAuthClient } from '$lib/apis/configs';
	import AccessControlModal from '$lib/components/workspace/common/AccessControlModal.svelte';
	import AccessButton from '$lib/components/common/AccessButton.svelte';
	import Spinner from '$lib/components/common/Spinner.svelte';
	import XMark from '$lib/components/icons/XMark.svelte';
	import Textarea from './common/Textarea.svelte';
	import ConfirmDialog from '$lib/components/common/ConfirmDialog.svelte';

	export let onSubmit: Function = () => {};
	export let onDelete: Function = () => {};

	export let show = false;
	export let edit = false;

	export let connection = null;

	let url = '';

	let auth_type = 'bearer';
	let key = '';
	let headers = '';

	let toolNameFilterList = '';
	let accessGrants = [];

	let id = '';
	let name = '';
	let description = '';

	let oauthClientInfo = null;

	let oauthClientId = '';
	let oauthClientSecret = '';
	let oauthServerUrl = '';
	let oauthScope = '';
	let oauthResourceParameter = 'auto';

	let enable = true;
	let loading = false;
	let showAdvanced = false;
	let showAccessControlModal = false;
	let showDeleteConfirmDialog = false;

	const inputClass =
		'bg-transparent outline-hidden placeholder:text-gray-300 dark:placeholder:text-gray-700';
	const selectClass =
		'bg-transparent pr-5 outline-hidden placeholder:text-gray-300 dark:placeholder:text-gray-700';

	const registerOAuthClientHandler = async () => {
		if (url === '') {
			toast.error($i18n.t('Please enter a valid URL'));
			return;
		}

		if (id === '') {
			toast.error($i18n.t('Please enter a valid ID'));
			return;
		}

		if (auth_type === 'oauth_2.1_static' && (!oauthClientId || !oauthClientSecret)) {
			toast.error($i18n.t('Please enter Client ID and Client Secret'));
			return;
		}

		// client_id is the tool server ID (used as the internal lookup key for both flows).
		// For static, client_secret signals the backend to use the static credential path.
		// The actual OAuth client_id/secret come from the connection info at save time.
		const formData: {
			url: string;
			client_id: string;
			client_secret?: string;
			oauth_server_url?: string;
			oauth_scope?: string;
		} = {
			url: url,
			client_id: id,
			...(oauthScope ? { oauth_scope: oauthScope } : {}),
			...(auth_type === 'oauth_2.1_static'
				? { client_secret: oauthClientSecret, oauth_server_url: oauthServerUrl }
				: {})
		};

		const res = await registerOAuthClient(localStorage.token, formData, 'mcp').catch((err) => {
			toast.error($i18n.t('Registration failed'));
			return null;
		});

		if (res) {
			toast.warning(
				$i18n.t(
					'Please save the connection to persist the OAuth client information and do not change the ID'
				)
			);
			toast.success($i18n.t('Registration successful'));

			console.debug('Registration successful', res);
			oauthClientInfo = res?.oauth_client_info ?? null;
		}
	};

	const verifyHandler = async () => {
		if (url === '') {
			toast.error($i18n.t('Please enter a valid URL'));
			return;
		}

		if (headers) {
			try {
				let _headers = JSON.parse(headers);
				if (typeof _headers !== 'object' || Array.isArray(_headers)) {
					_headers = null;
					throw new Error('Headers must be a valid JSON object');
				}
				headers = JSON.stringify(_headers, null, 2);
			} catch (error) {
				toast.error($i18n.t('Headers must be a valid JSON object'));
				return;
			}
		}

		const res = await verifyToolServerConnection(localStorage.token, {
			url,
			path: '',
			type: 'mcp',
			auth_type,
			headers: headers ? JSON.parse(headers) : undefined,
			key,
			config: {
				enable,
				access_grants: accessGrants
			},
			info: { id, name, description }
		}).catch(() => {
			toast.error($i18n.t('Connection failed'));
		});

		if (res) {
			toast.success($i18n.t('Connection successful'));
			console.debug('Connection successful', res);
		}
	};

	const submitHandler = async () => {
		loading = true;

		if (id.includes(':') || id.includes('|')) {
			toast.error($i18n.t('ID cannot contain ":" or "|" characters'));
			loading = false;
			return;
		}

		if (['oauth_2.1', 'oauth_2.1_static'].includes(auth_type) && !oauthClientInfo) {
			toast.error($i18n.t('Please register the OAuth client'));
			loading = false;
			return;
		}

		if (headers) {
			try {
				const _headers = JSON.parse(headers);
				if (typeof _headers !== 'object' || Array.isArray(_headers)) {
					throw new Error('Headers must be a valid JSON object');
				}
				headers = JSON.stringify(_headers, null, 2);
			} catch (error) {
				toast.error($i18n.t('Headers must be a valid JSON object'));
				loading = false;
				return;
			}
		}

		const connection = {
			type: 'mcp',
			url,
			path: '',

			auth_type,
			headers: headers ? JSON.parse(headers) : undefined,

			key,
			config: {
				enable: enable,
				function_name_filter_list: toolNameFilterList,
				access_grants: accessGrants
			},
			info: {
				id: id,
				name: name,
				description: description,
				...(['oauth_2.1', 'oauth_2.1_static'].includes(auth_type)
					? {
							...(oauthScope ? { oauth_scope: oauthScope } : {}),
							oauth_resource_parameter: oauthResourceParameter
						}
					: {}),
				...(oauthClientInfo ? { oauth_client_info: oauthClientInfo } : {}),
				...(auth_type === 'oauth_2.1_static'
					? {
							oauth_client_id: oauthClientId,
							oauth_client_secret: oauthClientSecret,
							oauth_server_url: oauthServerUrl
						}
					: {})
			}
		};

		await onSubmit(connection);

		loading = false;
		show = false;

		// reset form
		url = '';

		key = '';
		auth_type = 'bearer';

		id = '';
		name = '';
		description = '';

		oauthClientInfo = null;
		oauthClientId = '';
		oauthClientSecret = '';
		oauthServerUrl = '';
		oauthScope = '';
		oauthResourceParameter = 'auto';

		enable = true;
		toolNameFilterList = '';
		accessGrants = [];
	};

	const init = () => {
		if (connection) {
			url = connection.url;

			auth_type = connection?.auth_type ?? 'bearer';
			headers = connection?.headers ? JSON.stringify(connection.headers, null, 2) : '';

			key = connection?.key ?? '';

			id = connection.info?.id ?? '';
			name = connection.info?.name ?? '';
			description = connection.info?.description ?? '';
			oauthClientInfo = connection.info?.oauth_client_info ?? null;
			oauthClientId = connection.info?.oauth_client_id ?? '';
			oauthClientSecret = connection.info?.oauth_client_secret ?? '';
			oauthServerUrl = connection.info?.oauth_server_url ?? '';
			oauthScope = connection.info?.oauth_scope ?? '';
			oauthResourceParameter = connection.info?.oauth_resource_parameter ?? 'auto';

			enable = connection.config?.enable ?? true;
			toolNameFilterList = connection.config?.function_name_filter_list ?? '';
			accessGrants = connection.config?.access_grants ?? [];
		}
	};

	$: if (show) {
		init();
	}

	onMount(() => {
		init();
	});
</script>

<Modal size="sm" bind:show>
	<div>
		<div class=" flex justify-between dark:text-gray-100 px-5 pt-4 pb-2">
			<h1 class=" text-lg font-medium self-center font-primary">
				{#if edit}
					{$i18n.t('Edit Connection')}
				{:else}
					{$i18n.t('Add Connection')}
				{/if}
			</h1>

			<div class="flex items-center gap-3">
				<button
					class="self-center"
					aria-label={$i18n.t('Close Configure Connection Modal')}
					on:click={() => {
						show = false;
					}}
				>
					<XMark className={'size-5'} />
				</button>
			</div>

			<div class="flex flex-col md:flex-row w-full px-4 pb-4 md:space-x-4 dark:text-gray-200">
				<div class=" flex flex-col w-full sm:flex-row sm:justify-center sm:space-x-6">
					<form
						class="flex flex-col w-full"
						on:submit={(e) => {
							e.preventDefault();
							submitHandler();
						}}
					>
						<div class="px-1">
							<div class="flex gap-2 mb-1.5">
								<div class="flex w-full justify-between items-center">
									<div class=" text-xs text-gray-500">{$i18n.t('Type')}</div>

									<div class="text-xs text-gray-700 dark:text-gray-300">
										{$i18n.t('MCP')}
										<span class="text-gray-500">{$i18n.t('Streamable HTTP')}</span>
									</div>
								</div>
							</div>

							<div class="flex gap-2">
								<div class="flex flex-col flex-1">
									<div class="flex justify-between mb-0.5">
										<label for="enter-name" class={`text-xs text-gray-500`}>{$i18n.t('Name')}</label
										>
									</div>

									<div class="flex flex-1 items-center">
										<input
											id="enter-name"
											class={`w-full flex-1 text-sm ${inputClass}`}
											type="text"
											bind:value={name}
											placeholder={$i18n.t('Enter name')}
											autocomplete="off"
										/>
									</div>
								</div>
								<div class="flex flex-col flex-1">
									<div class="flex justify-between mb-0.5">
										<label for="enter-id" class={`text-xs text-gray-500`}>{$i18n.t('ID')}</label>
									</div>
									<div class="flex flex-1 items-center">
										<input
											id="enter-id"
											class={`w-full flex-1 text-sm font-mono ${inputClass}`}
											type="text"
											bind:value={id}
											placeholder="server-id"
											autocomplete="off"
											required
										/>
									</div>
								</div>
							</div>

							<div class="flex flex-col w-full mt-1 mb-1.5">
								<label for="description" class={`mb-0.5 text-xs text-gray-500`}
									>{$i18n.t('Description')}</label
								>

								<div class="flex-1">
									<input
										id="description"
										class={`w-full text-sm ${inputClass}`}
										type="text"
										bind:value={description}
										placeholder={$i18n.t('Enter description')}
										autocomplete="off"
									/>
								</div>
							</div>

							<div class="flex gap-2">
								<div class="flex flex-col w-full">
									<div class="flex justify-between mb-0.5">
										<label for="api-base-url" class={`text-xs text-gray-500`}
											>{$i18n.t('URL')}</label
										>
									</div>

									<div class="flex flex-1 items-center">
										<input
											id="api-base-url"
											class={`w-full flex-1 text-sm ${inputClass}`}
											type="text"
											bind:value={url}
											placeholder={$i18n.t('MCP Streamable HTTP URL')}
											autocomplete="off"
											required
										/>

										<Tooltip
											content={$i18n.t('Verify Connection')}
											className="shrink-0 flex items-center mr-1"
										>
											<button
												class="self-center p-1 bg-transparent hover:bg-gray-100 dark:hover:bg-gray-850 rounded-lg transition"
												on:click={() => {
													verifyHandler();
												}}
												aria-label={$i18n.t('Verify Connection')}
												type="button"
											>
												<svg
													xmlns="http://www.w3.org/2000/svg"
													viewBox="0 0 20 20"
													fill="currentColor"
													class="w-4 h-4"
													aria-hidden="true"
												>
													<path
														fill-rule="evenodd"
														d="M15.312 11.424a5.5 5.5 0 01-9.201 2.466l-.312-.311h2.433a.75.75 0 000-1.5H3.989a.75.75 0 00-.75.75v4.242a.75.75 0 001.5 0v-2.43l.31.31a7 7 0 0011.712-3.138.75.75 0 00-1.449-.39zm1.23-3.723a.75.75 0 00.219-.53V2.929a.75.75 0 00-1.5 0V5.36l-.31-.31A7 7 0 003.239 8.188a.75.75 0 101.448.389A5.5 5.5 0 0113.89 6.11l.311.31h-2.432a.75.75 0 000 1.5h4.243a.75.75 0 00.53-.219z"
														clip-rule="evenodd"
													/>
												</svg>
											</button>
										</Tooltip>

										<Tooltip content={enable ? $i18n.t('Enabled') : $i18n.t('Disabled')}>
											<Switch bind:state={enable} />
										</Tooltip>
									</div>
								</div>
							</div>

							<div class="flex gap-2 mt-2">
								<div class="flex flex-col w-full">
									<div class="flex justify-between items-center">
										<div class="flex gap-2 items-center">
											<div for="select-bearer-or-session" class={`text-xs text-gray-500`}>
												{$i18n.t('Auth')}
											</div>
										</div>

										{#if ['oauth_2.1', 'oauth_2.1_static'].includes(auth_type)}
											<div class="flex items-center gap-2">
												<div class="flex flex-col justify-end items-center shrink-0">
													<Tooltip
														content={oauthClientInfo
															? $i18n.t('Register Again')
															: $i18n.t('Register Client')}
													>
														<button
															class=" text-xs underline dark:text-gray-500 dark:hover:text-gray-200 text-gray-700 hover:text-gray-900 transition"
															type="button"
															on:click={() => {
																registerOAuthClientHandler();
															}}
														>
															{$i18n.t('Register Client')}
														</button>
													</Tooltip>
												</div>

												{#if !oauthClientInfo}
													<div
														class="text-xs font-normal px-1.5 rounded-md bg-yellow-500/20 text-yellow-700 dark:text-yellow-200"
													>
														{$i18n.t('Not Registered')}
													</div>
												{:else}
													<div
														class="text-xs font-normal px-1.5 rounded-md bg-green-500/20 text-green-700 dark:text-green-200"
													>
														{$i18n.t('Registered')}
													</div>
												{/if}
											</div>
										{/if}
									</div>

									<div class="flex gap-2">
										<div class="flex-shrink-0 self-start">
											<select
												id="select-bearer-or-session"
												class={`w-full text-sm ${selectClass}`}
												bind:value={auth_type}
											>
												<option value="none">{$i18n.t('None')}</option>

												<option value="bearer">{$i18n.t('Bearer')}</option>
												<option value="session">{$i18n.t('Session')}</option>

												<option value="system_oauth">{$i18n.t('OAuth')}</option>
												<option value="oauth_2.1">{$i18n.t('OAuth 2.1')}</option>
												<option value="oauth_2.1_static">{$i18n.t('OAuth 2.1 (Static)')}</option>
											</select>
										</div>

										<div class="flex flex-1 items-center">
											{#if auth_type === 'bearer'}
												<SensitiveInput
													bind:value={key}
													placeholder={$i18n.t('API Key')}
													required={false}
												/>
											{:else if auth_type === 'none'}
												<div class={`text-xs self-center translate-y-[1px] text-gray-500`}>
													{$i18n.t('No authentication')}
												</div>
											{:else if auth_type === 'session'}
												<div class={`text-xs self-center translate-y-[1px] text-gray-500`}>
													{$i18n.t('Forwards system user session credentials to authenticate')}
												</div>
											{:else if auth_type === 'system_oauth'}
												<div class={`text-xs self-center translate-y-[1px] text-gray-500`}>
													{$i18n.t('Forwards system user OAuth access token to authenticate')}
												</div>
											{:else if auth_type === 'oauth_2.1'}
												<div
													class={`flex items-center text-xs self-center translate-y-[1px] text-gray-500`}
												>
													{$i18n.t('Uses OAuth 2.1 Dynamic Client Registration')}
												</div>
											{:else if auth_type === 'oauth_2.1_static'}
												<div class="flex flex-col gap-1.5 w-full mt-0.5">
													<SensitiveInput
														bind:value={oauthClientId}
														placeholder={$i18n.t('Client ID')}
														required={false}
													/>
													<SensitiveInput
														bind:value={oauthClientSecret}
														placeholder={$i18n.t('Client Secret')}
														required={false}
													/>
													<div class="flex flex-1 items-center">
														<input
															class={`w-full text-sm ${inputClass}`}
															type="text"
															bind:value={oauthServerUrl}
															placeholder={$i18n.t('OAuth Server URL')}
															autocomplete="off"
														/>
													</div>
												</div>
											{/if}
										</div>
									</div>
								</div>
							</div>

							<div class="flex items-center justify-between">
								<button
									type="button"
									class="flex items-center gap-1 text-xs text-gray-500 dark:text-gray-400 hover:text-gray-700 dark:hover:text-gray-200 transition mt-2"
									on:click={() => (showAdvanced = !showAdvanced)}
								>
									<svg
										xmlns="http://www.w3.org/2000/svg"
										viewBox="0 0 20 20"
										fill="currentColor"
										class="w-3 h-3 transition-transform {showAdvanced ? 'rotate-90' : ''}"
									>
										<path
											fill-rule="evenodd"
											d="M7.21 14.77a.75.75 0 01.02-1.06L11.168 10 7.23 6.29a.75.75 0 111.04-1.08l4.5 4.25a.75.75 0 010 1.08l-4.5 4.25a.75.75 0 01-1.06-.02z"
											clip-rule="evenodd"
										/>
									</svg>
									{$i18n.t('Advanced')}
								</button>

								<AccessButton
									className="mt-2"
									on:click={() => {
										showAccessControlModal = true;
									}}
								/>
							</div>

							{#if showAdvanced}
								{#if ['oauth_2.1', 'oauth_2.1_static'].includes(auth_type)}
									<div class="flex gap-2 mt-2">
										<div class="flex flex-col w-full">
											<label for="oauth-scope" class={`mb-0.5 text-xs text-gray-500`}
												>{$i18n.t('OAuth Scopes')}</label
											>

											<div class="flex flex-1 items-center">
												<input
													id="oauth-scope"
													class={`w-full text-sm ${inputClass}`}
													type="text"
													bind:value={oauthScope}
													placeholder={$i18n.t('Use discovered scopes')}
													autocomplete="off"
												/>
											</div>
										</div>
									</div>

									<div class="flex gap-2 mt-2">
										<div class="flex flex-col w-full">
											<label for="oauth-resource-parameter" class={`mb-0.5 text-xs text-gray-500`}
												>{$i18n.t('OAuth Resource Parameter')}</label
											>

											<div class="flex flex-1 items-center">
												<select
													id="oauth-resource-parameter"
													class={`w-full text-sm ${selectClass}`}
													bind:value={oauthResourceParameter}
												>
													<option value="auto">{$i18n.t('Automatic')}</option>
													<option value="include">{$i18n.t('Include')}</option>
													<option value="omit">{$i18n.t('Omit')}</option>
												</select>
											</div>
										</div>
									</div>
								{/if}

								<div class="flex gap-2 mt-2">
									<div class="flex flex-col w-full">
										<label
											for="headers-input"
											class={`mb-0.5 text-xs text-gray-500
									`}>{$i18n.t('Headers')}</label
										>

										<div class="flex-1">
											<Tooltip
												content={$i18n.t(
													'Enter additional headers in JSON format (e.g. {"X-Custom-Header": "value"}'
												)}
											>
												<Textarea
													className="w-full text-sm outline-hidden"
													bind:value={headers}
													placeholder={$i18n.t('Enter additional headers in JSON format')}
													required={false}
													minSize={30}
												/>
											</Tooltip>
										</div>
									</div>
								</div>
							{/if}

							<hr class=" border-gray-100/50 dark:border-gray-700/10 my-2.5 w-full" />

							<div class="flex flex-col w-full mt-2">
								<label for="tool-name-filter-list" class={`mb-1 text-xs text-gray-500`}
									>{$i18n.t('MCP Tool Name Filter')}</label
								>

								<div class="flex-1">
									<input
										id="tool-name-filter-list"
										class={`w-full text-sm ${inputClass}`}
										type="text"
										bind:value={toolNameFilterList}
										placeholder={$i18n.t(
											'Enter tool names to include or exclude (e.g. read_file, !delete_file)'
										)}
										autocomplete="off"
									/>
								</div>
							</div>
						</div>

						<div class="flex justify-between items-center pt-3 text-sm font-medium">
							<div>
								{#if edit}
									<button
										class="px-1 py-1.5 text-sm font-medium text-gray-500 hover:text-gray-700 dark:text-gray-400 dark:hover:text-gray-200 hover:underline transition"
										type="button"
										on:click={() => {
											showDeleteConfirmDialog = true;
										}}
									>
										{$i18n.t('Delete')}
									</button>
								{/if}
							</div>

							<button
								class="px-3.5 py-1.5 text-sm font-medium bg-black hover:bg-gray-900 text-white dark:bg-white dark:text-black dark:hover:bg-gray-100 transition rounded-full flex items-center gap-2 whitespace-nowrap {loading
									? ' cursor-not-allowed'
									: ''}"
								type="submit"
								disabled={loading}
							>
								{$i18n.t('Save')}

								{#if loading}
									<span class="shrink-0">
										<Spinner />
									</span>
								{/if}
							</button>
						</div>
					</form>
				</div>
			</div>
		</div>
	</div></Modal
>

<AccessControlModal bind:show={showAccessControlModal} bind:accessGrants />

<ConfirmDialog
	bind:show={showDeleteConfirmDialog}
	message={$i18n.t(
		'Are you sure you want to delete this connection? This action cannot be undone.'
	)}
	confirmLabel={$i18n.t('Delete')}
	on:confirm={() => {
		onDelete();
		show = false;
	}}
/>
