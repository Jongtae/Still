(() => {
    'use strict';

    // Event protocol from telegram.org/js/telegram-web-app.js (Last-Modified 2026-11-09).
    // Upstream SHA-256: baa788a6f686f4238db1b82ce8e76470930af7411b18b521ebb95a2c1480a267
    function postEvent(eventType, eventData = '') {
        if (window.TelegramWebviewProxy !== undefined) {
            window.TelegramWebviewProxy.postEvent(eventType, JSON.stringify(eventData));
        } else if (window.external && 'notify' in window.external) {
            window.external.notify(JSON.stringify({ eventType, eventData }));
        } else if (window.parent !== window) {
            window.parent.postMessage(JSON.stringify({ eventType, eventData }), 'https://web.telegram.org');
        } else {
            return false;
        }
        return true;
    }

    function sendData(data) {
        const size = new TextEncoder().encode(data).byteLength;
        return size > 0 && size <= 4096 && postEvent('web_app_data_send', { data });
    }

    const args = new URLSearchParams(window.location.search).getAll('s');
    if (args.length !== 1 || !/^[A-Za-z0-9_-]+$/.test(args[0])) return;
    let spec;
    try {
        const bytes = Uint8Array.from(atob(args[0].replace(/-/g, '+').replace(/_/g, '/')),
            char => char.charCodeAt(0));
        spec = JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(bytes));
    } catch {
        return;
    }
    if (spec?.v !== 1 || !Array.isArray(spec.fields)) return;

    document.documentElement.lang = spec.lang;
    document.title = document.getElementById('title').textContent = spec.title;
    const status = document.getElementById('status');
    try {
        if (!postEvent('web_app_ready') || !postEvent('web_app_expand') || !globalThis.crypto?.subtle) {
            status.textContent = spec.no_bridge;
            return;
        }
    } catch {
        status.textContent = spec.no_bridge;
        return;
    }

    const fields = document.getElementById('fields');
    const inputs = spec.fields.map(({ name, label }) => {
        const row = document.createElement('label');
        row.textContent = label;
        const input = document.createElement('input');
        input.type = 'password';
        input.autocomplete = 'off';
        input.spellcheck = false;
        row.append(input);
        fields.append(row);
        return [name, input];
    });

    const save = document.getElementById('save');
    save.textContent = spec.save;
    save.hidden = false;
    save.onclick = async () => {
        save.disabled = true;
        status.textContent = '';
        const body = Object.fromEntries(inputs.map(([name, input]) => [name, input.value]));
        try {
            const plaintext = JSON.stringify(body);
            // Even before encryption, this much plaintext cannot fit in a Telegram submission.
            if (new TextEncoder().encode(plaintext).byteLength > 4096) {
                status.textContent = spec.too_long;
                return;
            }
            const token = await globalThis.stillSeal(spec.k, spec.kid, plaintext);
            if (new TextEncoder().encode(token).byteLength > 4096) {
                status.textContent = spec.too_long;
                return;
            }
            for (const [, input] of inputs) input.value = '';
            if (sendData(token)) return;
            status.textContent = spec.no_bridge;
        } catch {
            status.textContent = spec.no_bridge;
        } finally {
            if (status.textContent) save.disabled = false;
        }
    };
})();
