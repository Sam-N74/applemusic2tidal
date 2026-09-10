// export_apple_music.js
// À coller dans la console (F12 → Console) sur https://music.apple.com, connecté.
// Chrome demande de taper "allow pasting" la première fois : fais-le, puis recolle.
// Télécharge apple_library.json à la fin. Compte ~1 s par 100 titres.

(async () => {
  const LIMIT = 100;
  const log = (...a) => console.log("[export]", ...a);

  // ---------- Accès API : MusicKit si dispo, sinon tokens bruts ----------
  let call;
  if (window.MusicKit && MusicKit.getInstance?.()) {
    const mk = MusicKit.getInstance();
    call = async (path, params) => (await mk.api.music(path, params)).data;
    log("via MusicKit");
  } else {
    // developer token = JWT dans un bundle JS ; user token = cookie media-user-token
    const userToken = document.cookie.match(/media-user-token=([^;]+)/)?.[1];
    if (!userToken) throw new Error("Pas connecté (cookie media-user-token absent)");
    let devToken = null;
    for (const s of [...document.scripts].map(s => s.src).filter(Boolean)) {
      const txt = await fetch(s).then(r => r.text()).catch(() => "");
      const m = txt.match(/"(eyJ[\w-]{10,}\.[\w-]{10,}\.[\w-]{10,})"/);
      if (m) { devToken = m[1]; break; }
    }
    if (!devToken) throw new Error("Developer token introuvable");
    call = async (path, params) => {
      const url = new URL("https://amp-api.music.apple.com" + path);
      Object.entries(params || {}).forEach(([k, v]) => url.searchParams.set(k, v));
      const r = await fetch(url, {
        headers: { Authorization: "Bearer " + devToken, "media-user-token": decodeURIComponent(userToken), Origin: "https://music.apple.com" },
      });
      if (!r.ok) throw new Error(r.status + " " + url);
      return r.json();
    };
    log("via tokens bruts");
  }

  const all = async (path, extra = {}) => {
    const out = [];
    for (let offset = 0; ; offset += LIMIT) {
      let page;
      try { page = await call(path, { limit: LIMIT, offset, ...extra }); }
      catch (e) { if (String(e).includes("429")) { await new Promise(r => setTimeout(r, 3000)); offset -= LIMIT; continue; } throw e; }
      out.push(...(page.data || []));
      if (!page.next || !page.data?.length) break;
    }
    return out;
  };

  const song = (s) => {
    const a = s.attributes || {};
    const cat = s.relationships?.catalog?.data?.[0]?.attributes || {};
    return {
      id: s.id,
      name: a.name || "",
      artist: a.artistName || "",
      album: a.albumName || "",
      duration_ms: a.durationInMillis || cat.durationInMillis || 0,
      isrc: cat.isrc || null,
      catalog_id: a.playParams?.catalogId || s.relationships?.catalog?.data?.[0]?.id || null,
      year: (cat.releaseDate || "").slice(0, 4) || null,
    };
  };

  // ---------- Bibliothèque ----------
  log("titres…");
  const songs = (await all("/v1/me/library/songs", { include: "catalog" })).map(song);
  log(songs.length, "titres");

  // ---------- Titres aimés (rating 1 = love) ----------
  log("ratings…");
  const loved = new Set();
  for (let i = 0; i < songs.length; i += 100) {
    const ids = songs.slice(i, i + 100).map(s => s.id).join(",");
    try {
      const r = await call("/v1/me/ratings/library-songs", { ids });
      (r.data || []).forEach(x => { if (x.attributes?.value === 1) loved.add(x.id); });
    } catch (e) { log("ratings indisponibles pour ce lot:", String(e)); }
  }
  songs.forEach(s => (s.loved = loved.has(s.id)));
  log(loved.size, "titres aimés");

  // ---------- Playlists ----------
  log("playlists…");
  const playlists = [];
  const pls = await all("/v1/me/library/playlists");
  for (const p of pls) {
    const a = p.attributes || {};
    let tracks = [];
    try { tracks = (await all(`/v1/me/library/playlists/${p.id}/tracks`, { include: "catalog" })).map(song); }
    catch (e) { log("playlist vide/inaccessible:", a.name); }
    playlists.push({ id: p.id, name: a.name, editable: a.canEdit ?? true, description: a.description?.standard || "", tracks });
    log(`  ${a.name} (${tracks.length})`);
  }

  // ---------- Albums de la bibliothèque ----------
  log("albums…");
  const albums = (await all("/v1/me/library/albums", { include: "catalog" })).map(al => {
    const a = al.attributes || {}, cat = al.relationships?.catalog?.data?.[0]?.attributes || {};
    return { id: al.id, name: a.name, artist: a.artistName, track_count: a.trackCount, upc: cat.upc || null, catalog_id: al.relationships?.catalog?.data?.[0]?.id || null };
  });

  const blob = new Blob([JSON.stringify({ exported_at: new Date().toISOString(), songs, playlists, albums }, null, 1)], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  Object.assign(document.createElement("a"), { href: url, download: "apple_library.json" }).click();
  log("OK →", songs.length, "titres,", playlists.length, "playlists,", albums.length, "albums");
})();
