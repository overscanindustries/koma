<script>
  // Settings: a left list on a desktop, a list page then one section at a time on
  // a phone. Each section: one page title, a lede only when the consequence is not obvious, rows on --bg with hairlines.
  import {route, SETTINGS_NAV, screen, go, theme, setTheme} from '../lib/ui.svelte.js';
  import {cam, connected} from '../lib/camera.svelte.js';
  import {S, B, readFromCamera, pending} from '../lib/settings.svelte.js';
  import {setupEntry} from '../lib/onboard.svelte.js';
  import BackLink from '../components/BackLink.svelte';
  import PendingBar from '../components/PendingBar.svelte';
  import PageTitle from '../components/ui/PageTitle.svelte';
  import Row from '../components/ui/Row.svelte';
  import Segmented from '../components/ui/Segmented.svelte';
  import Loading from '../components/ui/Loading.svelte';
  import ErrorState from '../components/ui/ErrorState.svelte';
  import CameraMenu from './settings/CameraMenu.svelte';
  import KomaMode from './settings/KomaMode.svelte';
  import Backup from './settings/Backup.svelte';
  import Copy from './settings/Copy.svelte';
  import Update from './settings/Update.svelte';
  import Advanced from './settings/Advanced.svelte';
  import PhoneAccess from './phone/PhoneAccess.svelte';

  // On a phone, Settings opens on its list; on a desktop, on the Camera menu.
  const sub = $derived(route.sub || (screen.phone ? '' : 'camera'));
  const item = $derived(SETTINGS_NAV.find(n => n.id === sub));
  const hasBlock = $derived.by(() => { void S.rev; return !!B.blk; });
  const needsBlock = $derived(sub === 'camera' || sub === 'mode');
  const showBar = $derived(sub === 'camera' || sub === 'mode' || sub === 'advanced');
  const hasPending = $derived.by(() => { void S.rev; return pending().length > 0 || B.blk && B.orig && B.blk.some((x, i) => x !== B.orig[i]); });
  const LEDE = {
    mode: 'How the camera lays out its menu, and how much of Koma runs.',
    copy: "One GR's settings onto another. Each camera keeps its own lens data and counters.",
    advanced: 'The 4 KB settings block, field by field and byte by byte.',
  };

  // Read the camera's settings when a section needs them: first time, or a new connection with nothing pending.
  $effect(() => {
    if (!(needsBlock || sub === 'backup' || sub === 'advanced')) return;
    if (!connected() || S.source === 'card' || S.reading || S.map !== 'ready') return;
    if (S.readError && S.readFailConn === cam.conn) return;  // failed on this connection: Try again reads
    if (S.source !== 'camera' || (S.readConn !== cam.conn && pending().length === 0)) readFromCamera();
  });
  const themeChoice = $derived(theme.choice === 'light' || theme.choice === 'dark' ? theme.choice : 'system');
  // A read that failed: on the official firmware the way on is setup (Set up Koma / Continue setup); otherwise read
  // again.
  const readAction = $derived(S.readKind === 'stock' ? setupLink() : {label: 'Try again', onclick: readFromCamera});
  function setupLink() { const e = setupEntry(); return e ? {...e, kind: 'link', testid: 'setup-entry'} : null; }
</script>

<div class="settings" class:list={!sub} class:withbar={showBar && hasPending}>
  {#if !screen.phone || !sub}
    <nav class="subnav" aria-label="Settings">
      {#if screen.phone}<PageTitle title="Settings" />{/if}
      {#if screen.phone}
        <div class="rows">
          {#each SETTINGS_NAV as n}
            {#if n.divider}<div class="div" aria-hidden="true"></div>{/if}
            <a class="prow" href={'#/settings/' + n.id} data-settings={n.id}>
              <Row label={n.label} chevron />
            </a>
          {/each}
        </div>
        <div class="appearance">
          <span id="appearance-l">Appearance</span>
          <Segmented name="theme" labelledby="appearance-l" value={themeChoice} onchange={setTheme} testid="theme-choice"
            options={[{value: 'dark', label: 'Dark'}, {value: 'light', label: 'Light'}, {value: 'system', label: 'System'}]} />
        </div>
      {:else}
        <ul>
          {#each SETTINGS_NAV as n}
            {#if n.divider}<li class="div" aria-hidden="true"></li>{/if}
            <li>
              <a href={'#/settings/' + n.id} aria-current={sub === n.id ? 'page' : undefined} data-settings={n.id}>{n.label}</a>
            </li>
          {/each}
        </ul>
      {/if}
    </nav>
  {/if}

  {#if sub}
    <section class="section" aria-labelledby="set-title">
      {#if screen.phone}
        <BackLink label="Settings" onclick={() => go('settings')} />
      {/if}
      <PageTitle id="set-title" title={item ? item.label : ''} lede={LEDE[sub] || ''}>
        {#snippet actions()}
          <!-- kept in the layout while an edit is pending, so the title row and the tab strip do not move -->
          {#if sub === 'camera' && S.source === 'camera' && hasBlock}
            <button class="btn sm reread" class:held={hasPending} type="button" onclick={readFromCamera}
              disabled={S.reading || hasPending} aria-hidden={hasPending ? 'true' : undefined}
              tabindex={hasPending ? -1 : undefined} data-testid="reread">Read the camera again</button>
          {/if}
        {/snippet}
      </PageTitle>

      {#if needsBlock && S.map === 'error'}
        <ErrorState text="The settings list did not load. Back up and restore still work." />
      {:else if needsBlock && !hasBlock}
        {#if S.readError}
          <ErrorState text={S.readError} action={readAction} testid="read-error" />
        {:else}
          <Loading text="Reading the settings from the camera." />
        {/if}
      {:else if sub === 'camera'}
        <CameraMenu />
      {:else if sub === 'mode'}
        <KomaMode />
      {:else if sub === 'backup'}
        <Backup />
      {:else if sub === 'copy'}
        <Copy />
      {:else if sub === 'update'}
        <Update />
      {:else if sub === 'advanced'}
        <Advanced />
      {:else if sub === 'phone'}
        <PhoneAccess />
      {/if}
    </section>
  {/if}
</div>

{#if showBar}<PendingBar />{/if}

<style>
  .settings { display: grid; grid-template-columns: 176px minmax(0, 1fr); column-gap: var(--s7); align-items: start; max-width: var(--content-max); }
  .reread { color: var(--sub); }
  .reread:hover { color: var(--ink); }
  .reread.held { visibility: hidden; }
  .subnav { position: sticky; top: 40px; padding-top: 4px; }
  .subnav ul { list-style: none; margin: 0; padding: 0; display: grid; }
  .subnav ul a {
    position: relative; display: flex; align-items: center;
    min-height: 36px; padding: 0;
    color: var(--sub); text-decoration: none; font-size: var(--fs-body); line-height: var(--lh-body);
    transition: color var(--t-fast) var(--ease);
  }
  .subnav ul a:hover { color: var(--ink); }
  /* the current page in ink and weight only; amber stays with the tab bar and the shutter */
  .subnav ul a[aria-current] { color: var(--ink); font-weight: 560; }
  .subnav ul a:focus-visible { outline-offset: -2px; }
  .subnav li.div { height: 1px; background: var(--line); margin: var(--s3) 0; }
  .section { min-width: 0; max-width: 820px; }
  /* The pending bar is pinned to the bottom: room for it under the last row. */
  .settings.withbar { padding-bottom: 72px; }

  /* phone: the list page. Every row carries one rule, under it; a group starts after 32 px of open space, so no gap
     is boxed between two rules (it read as an empty row). */
  .prow { display: block; color: inherit; text-decoration: none; }
  .prow:focus-visible { outline-offset: -2px; }
  .prow:hover :global(.chev) { color: var(--ink); transform: translateX(2px); }
  .rows .div { height: var(--s6); }
  .appearance {
    display: flex; align-items: center; justify-content: space-between; gap: var(--s4);
    margin-top: var(--s6); padding: var(--s2) 0; border-bottom: 1px solid var(--line);
    font-weight: 500;
  }

  @media (max-width: 720px) {
    .settings { grid-template-columns: minmax(0, 1fr); gap: 0; }
    .subnav { position: static; }
    .settings.withbar { padding-bottom: 64px; }
  }
</style>
