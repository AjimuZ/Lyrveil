// T-SEC-09 lint rule: no HTML from strings and no sanitizer bypass, in TypeScript and in Angular templates.
//
// Each list is the option array of ESLint's core no-restricted-syntax rule, applied by eslint.config.mjs to its
// file type. Messages render as text through bindings (docs/frontend.md, Security in the browser), so nothing in
// the app ever needs these APIs.

const htmlProperty = '/^(inner|outer)HTML$/i';
const reason = 'Render text through Angular bindings: see docs/frontend.md, Security in the browser.';

/** Restrictions for TypeScript sources, including the host bindings of components and directives. */
export const typeScriptRestrictions = [
  {
    selector: `MemberExpression[property.name=${htmlProperty}]`,
    message: `innerHTML and outerHTML are forbidden. ${reason}`,
  },
  {
    selector: 'MemberExpression[property.name="insertAdjacentHTML"]',
    message: `insertAdjacentHTML is forbidden. ${reason}`,
  },
  {
    selector: 'Identifier[name=/^bypassSecurityTrust/]',
    message: `bypassSecurityTrust* is forbidden: the sanitizer is never bypassed. ${reason}`,
  },
  {
    selector: 'Property > Literal.key[value=/^\\[(inner|outer)HTML\\]$/i]',
    message: `An innerHTML or outerHTML host binding is forbidden. ${reason}`,
  },
  {
    selector: `Decorator > CallExpression[callee.name="HostBinding"] > Literal[value=${htmlProperty}]`,
    message: `An innerHTML or outerHTML host binding is forbidden. ${reason}`,
  },
];

/** Restrictions for Angular templates, in .html files and inline templates alike. */
export const templateRestrictions = [
  {
    selector: `BoundAttribute[name=${htmlProperty}]`,
    message: `An [innerHTML] or [outerHTML] binding is forbidden. ${reason}`,
  },
];
