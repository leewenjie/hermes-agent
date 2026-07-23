// jsdom does not currently implement CSS.escape. Keep the shim here instead
// of weakening production selectors or repeating it in every renderer test.
globalThis.CSS ??= {} as typeof CSS

if (typeof globalThis.CSS.escape !== 'function') {
  globalThis.CSS.escape = value => {
    const input = String(value)
    let escaped = ''

    for (let index = 0; index < input.length; index += 1) {
      const code = input.charCodeAt(index)
      const character = input.charAt(index)

      if (code === 0) {
        escaped += '\uFFFD'

        continue
      }

      if (
        (code >= 1 && code <= 31) ||
        code === 127 ||
        (index === 0 && code >= 48 && code <= 57) ||
        (index === 1 && code >= 48 && code <= 57 && input.charCodeAt(0) === 45)
      ) {
        escaped += `\\${code.toString(16)} `

        continue
      }

      if (index === 0 && code === 45 && input.length === 1) {
        escaped += '\\-'

        continue
      }

      if (code >= 128 || code === 45 || code === 95 || (code >= 48 && code <= 57) || (code >= 65 && code <= 90) || (code >= 97 && code <= 122)) {
        escaped += character
      } else {
        escaped += `\\${character}`
      }
    }

    return escaped
  }
}