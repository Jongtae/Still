globalThis.stillSeal = async function (spkiB64url, kid, plaintextString) {
    const { subtle } = globalThis.crypto;
    const utf8 = new TextEncoder();
    const b64url = bytes => btoa(String.fromCharCode(...bytes))
        .replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');

    const spki = Uint8Array.from(atob(spkiB64url.replace(/-/g, '+').replace(/_/g, '/')),
        char => char.charCodeAt(0));
    const rsa = await subtle.importKey('spki', spki, { name: 'RSA-OAEP', hash: 'SHA-256' },
        false, ['wrapKey']);
    const cek = await subtle.generateKey({ name: 'AES-GCM', length: 256 }, true, ['encrypt']);
    const header = b64url(utf8.encode(JSON.stringify({ alg: 'RSA-OAEP-256', enc: 'A256GCM', kid })));
    const iv = globalThis.crypto.getRandomValues(new Uint8Array(12));
    const sealed = new Uint8Array(await subtle.encrypt({
        name: 'AES-GCM', iv, additionalData: utf8.encode(header), tagLength: 128,
    }, cek, utf8.encode(plaintextString)));
    const wrapped = new Uint8Array(await subtle.wrapKey('raw', cek, rsa, { name: 'RSA-OAEP' }));
    return [header, ...[wrapped, iv, sealed.subarray(0, -16), sealed.subarray(-16)].map(b64url)].join('.');
};
