<script lang="ts">
	import { safeImageUrl } from '$lib/utils/safeImageUrl';

	export let className = 'size-8';
	export let src = `/static/favicon.png`;
</script>

<img
	aria-hidden="true"
	src={safeImageUrl(src)}
	class=" {className} object-cover rounded-2xl"
	alt="profile"
	draggable="false"
/>
