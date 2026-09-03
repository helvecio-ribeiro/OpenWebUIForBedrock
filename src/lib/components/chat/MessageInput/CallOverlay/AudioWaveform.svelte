<script lang="ts">
	export let levels: number[] = [];
	export let compact = false;

	// Put the low-frequency voice energy in the middle and mirror progressively
	// higher bands toward the edges, like a conventional voice waveform.
	$: center = (levels.length - 1) / 2;
	$: displayLevels = levels.map((_, index) => {
		const distance = Math.abs(index - center);
		const sourceIndex = Math.min(levels.length - 1, Math.round(distance));
		const envelope = 1 - (distance / Math.max(1, center)) * 0.62;
		return Math.max(0.1, (levels[sourceIndex] ?? 0.1) * envelope);
	});
</script>

<div class:compact class="waveform" role="img" aria-label="Audio playback waveform">
	{#each displayLevels as level, index}
		<span
			style={`height: ${Math.max(10, level * 100)}%; opacity: ${0.55 + level * 0.45}; transition-delay: ${index * 8}ms;`}
		></span>
	{/each}
</div>

<style>
	.waveform {
		display: flex;
		align-items: center;
		justify-content: center;
		gap: 0.45rem;
		width: 11rem;
		height: 11rem;
	}

	.waveform.compact {
		gap: 0.16rem;
		width: 3rem;
		height: 3rem;
	}

	span {
		display: block;
		width: 0.55rem;
		max-height: 88%;
		border-radius: 9999px;
		background: currentColor;
		transition:
			height 70ms linear,
			opacity 70ms linear;
	}

	.compact span {
		width: 0.2rem;
	}
</style>
