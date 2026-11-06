// Fails when a stylesheet under src/ holds a color literal: only src/styles/tokens.scss may define colors.
//
// Usage: node tools/check-styles.mjs. Exit status 0 when every stylesheet uses token colors only, 1 otherwise.
// Component styles live in .scss files: eslint.config.mjs forbids inline `styles` and template style attributes,
// so these files are the only place a color can appear.
import { readFileSync, readdirSync } from 'node:fs';
import { join, relative, sep } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

/** The one stylesheet allowed to hold color literals, relative to the frontend folder. */
export const TOKEN_FILE = 'src/styles/tokens.scss';

// CSS named colors (CSS Color Module Level 4). `transparent` and `currentColor` are not literals and stay allowed.
const NAMED_COLORS = new Set(
  (
    'aliceblue antiquewhite aqua aquamarine azure beige bisque black blanchedalmond blue blueviolet brown burlywood ' +
    'cadetblue chartreuse chocolate coral cornflowerblue cornsilk crimson cyan darkblue darkcyan darkgoldenrod ' +
    'darkgray darkgreen darkgrey darkkhaki darkmagenta darkolivegreen darkorange darkorchid darkred darksalmon ' +
    'darkseagreen darkslateblue darkslategray darkslategrey darkturquoise darkviolet deeppink deepskyblue dimgray ' +
    'dimgrey dodgerblue firebrick floralwhite forestgreen fuchsia gainsboro ghostwhite gold goldenrod gray green ' +
    'greenyellow grey honeydew hotpink indianred indigo ivory khaki lavender lavenderblush lawngreen lemonchiffon ' +
    'lightblue lightcoral lightcyan lightgoldenrodyellow lightgray lightgreen lightgrey lightpink lightsalmon ' +
    'lightseagreen lightskyblue lightslategray lightslategrey lightsteelblue lightyellow lime limegreen linen ' +
    'magenta maroon mediumaquamarine mediumblue mediumorchid mediumpurple mediumseagreen mediumslateblue ' +
    'mediumspringgreen mediumturquoise mediumvioletred midnightblue mintcream mistyrose moccasin navajowhite navy ' +
    'oldlace olive olivedrab orange orangered orchid palegoldenrod palegreen paleturquoise palevioletred ' +
    'papayawhip peachpuff peru pink plum powderblue purple rebeccapurple red rosybrown royalblue saddlebrown ' +
    'salmon sandybrown seagreen seashell sienna silver skyblue slateblue slategray slategrey snow springgreen ' +
    'steelblue tan teal thistle tomato turquoise violet wheat white whitesmoke yellow yellowgreen'
  ).split(' '),
);

const HEX = /#(?:[0-9a-f]{8}|[0-9a-f]{6}|[0-9a-f]{3,4})\b/gi;
const COLOR_FUNCTION = /\b(?:rgba?|hsla?|hwb|lab|lch|oklab|oklch|color)\(/gi;
// A declaration's value, from the colon to the end of the declaration or block.
const DECLARATION = /[a-z-]+\s*:\s*([^;{}]+)/gi;

/** Replaces comments and quoted strings with spaces, keeping line breaks so reported lines stay exact. */
function blankNonCode(text) {
  const blank = (match) => match.replace(/[^\n]/g, ' ');
  return text
    .replace(/\/\*[\s\S]*?\*\//g, blank)
    .replace(/(^|[^:])\/\/[^\n]*/g, (match, before) => before + blank(match.slice(before.length)))
    .replace(/"[^"\n]*"|'[^'\n]*'/g, blank);
}

function lineOf(text, index) {
  return text.slice(0, index).split('\n').length;
}

/**
 * Returns every color literal in a stylesheet's text as { line, literal }, in source order: hex colors, color
 * functions such as rgb( or oklch(, and named colors inside declaration values. Comments and strings are ignored.
 * color-mix( is allowed, because it only combines tokens.
 */
export function findColorLiterals(text) {
  const code = blankNonCode(text);
  const found = [];
  for (const pattern of [HEX, COLOR_FUNCTION]) {
    for (const match of code.matchAll(pattern)) {
      found.push({ index: match.index, literal: match[0] });
    }
  }
  for (const declaration of code.matchAll(DECLARATION)) {
    const valueStart = declaration.index + declaration[0].length - declaration[1].length;
    for (const word of declaration[1].matchAll(/[a-z]+(?:-[a-z]+)*/gi)) {
      if (NAMED_COLORS.has(word[0].toLowerCase())) {
        found.push({ index: valueStart + word.index, literal: word[0] });
      }
    }
  }
  return found.sort((a, b) => a.index - b.index).map(({ index, literal }) => ({ line: lineOf(code, index), literal }));
}

/** Returns "file:line: literal" for every color literal in the stylesheets under root/src except the token file. */
export function checkStyles(root) {
  const failures = [];
  const source = join(root, 'src');
  for (const entry of readdirSync(source, { recursive: true })) {
    const path = join(source, entry);
    const name = relative(root, path).split(sep).join('/');
    if (!/\.(s?css)$/.test(name) || name === TOKEN_FILE) {
      continue;
    }
    for (const { line, literal } of findColorLiterals(readFileSync(path, 'utf8'))) {
      failures.push(`${name}:${line}: color literal ${literal}; use a token of ${TOKEN_FILE}`);
    }
  }
  return failures;
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  const root = fileURLToPath(new URL('..', import.meta.url));
  const failures = checkStyles(root);
  for (const failure of failures) {
    console.error(failure);
  }
  console.log(`Style colors: ${failures.length} color literal(s) outside ${TOKEN_FILE}`);
  process.exitCode = failures.length === 0 ? 0 : 1;
}
