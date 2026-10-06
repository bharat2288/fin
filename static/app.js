/* fin — the household's books, kept mostly through Claude and checked here.
 *
 * Four places (fin-surfaces 02): Home, Queue, Books, Changes. Every figure is
 * read from fin's own API; nothing here works out a balance, a tie or a
 * check of its own. What the screens add is how it is shown:
 *   - every amount carries its currency; rupees use Indian grouping
 *     (₹ 10,84,000.00), S$ and the rest western; owed is negative;
 *   - money out and money in are kept apart wherever something waits;
 *   - books are never added together: each has its own total;
 *   - trust markers (ties, off by, not checked, your figure, stale,
 *     unexplained, refused) are always shown, each with a word, and each
 *     links to its fix.
 * Vanilla JS, no build step.
 */
'use strict';

// A figure you entered that is older than this is stale: it waits in the queue.
const STALE_DAYS = 60;
// The balance sheet starts here (balance_sheet.START).
const SHEET_START = '2026-01';
// A missed bill: no payment seen this many days after it was due.
const BILL_GRACE_DAYS = 7;

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

// Icons: Phosphor Regular v2.1.1 (MIT), each the inside of a 256x256 <svg>, filled
// in currentColor. Copied from folio's static/js/kit/icons.js; those marked
// upstream are Phosphor's own (same version), which folio's set lacks.
const ICONS = Object.freeze({
    'list-bullets': '<path d="M80,64a8,8,0,0,1,8-8H216a8,8,0,0,1,0,16H88A8,8,0,0,1,80,64Zm136,56H88a8,8,0,0,0,0,16H216a8,8,0,0,0,0-16Zm0,64H88a8,8,0,0,0,0,16H216a8,8,0,0,0,0-16ZM44,52A12,12,0,1,0,56,64,12,12,0,0,0,44,52Zm0,64a12,12,0,1,0,12,12A12,12,0,0,0,44,116Zm0,64a12,12,0,1,0,12,12A12,12,0,0,0,44,180Z"/>',
    'scales': '<path d="M239.43,133l-32-80h0a8,8,0,0,0-9.16-4.84L136,62V40a8,8,0,0,0-16,0V65.58L54.26,80.19A8,8,0,0,0,48.57,85h0v.06L16.57,165a7.92,7.92,0,0,0-.57,3c0,23.31,24.54,32,40,32s40-8.69,40-32a7.92,7.92,0,0,0-.57-3L66.92,93.77,120,82V208H104a8,8,0,0,0,0,16h48a8,8,0,0,0,0-16H136V78.42L187,67.1,160.57,133a7.92,7.92,0,0,0-.57,3c0,23.31,24.54,32,40,32s40-8.69,40-32A7.92,7.92,0,0,0,239.43,133ZM56,184c-7.53,0-22.76-3.61-23.93-14.64L56,109.54l23.93,59.82C78.76,180.39,63.53,184,56,184Zm144-32c-7.53,0-22.76-3.61-23.93-14.64L200,77.54l23.93,59.82C222.76,148.39,207.53,152,200,152Z"/>',
    'arrow-counter-clockwise': '<path d="M224,128a96,96,0,0,1-94.71,96H128A95.38,95.38,0,0,1,62.1,197.8a8,8,0,0,1,11-11.63A80,80,0,1,0,71.43,71.39a3.07,3.07,0,0,1-.26.25L44.59,96H72a8,8,0,0,1,0,16H24a8,8,0,0,1-8-8V56a8,8,0,0,1,16,0V85.8L60.25,60A96,96,0,0,1,224,128Z"/>',
    'check-circle': '<path d="M173.66,98.34a8,8,0,0,1,0,11.32l-56,56a8,8,0,0,1-11.32,0l-24-24a8,8,0,0,1,11.32-11.32L112,148.69l50.34-50.35A8,8,0,0,1,173.66,98.34ZM232,128A104,104,0,1,1,128,24,104.11,104.11,0,0,1,232,128Zm-16,0a88,88,0,1,0-88,88A88.1,88.1,0,0,0,216,128Z"/>',
    'prohibit': '<path d="M128,24A104,104,0,1,0,232,128,104.11,104.11,0,0,0,128,24Zm88,104a87.56,87.56,0,0,1-20.41,56.28L71.72,60.4A88,88,0,0,1,216,128ZM40,128A87.56,87.56,0,0,1,60.41,71.72L184.28,195.6A88,88,0,0,1,40,128Z"/>',
    'warning': '<path d="M236.8,188.09,149.35,36.22h0a24.76,24.76,0,0,0-42.7,0L19.2,188.09a23.51,23.51,0,0,0,0,23.72A24.35,24.35,0,0,0,40.55,224h174.9a24.35,24.35,0,0,0,21.33-12.19A23.51,23.51,0,0,0,236.8,188.09ZM222.93,203.8a8.5,8.5,0,0,1-7.48,4.2H40.55a8.5,8.5,0,0,1-7.48-4.2,7.59,7.59,0,0,1,0-7.72L120.52,44.21a8.75,8.75,0,0,1,15,0l87.45,151.87A7.59,7.59,0,0,1,222.93,203.8ZM120,144V104a8,8,0,0,1,16,0v40a8,8,0,0,1-16,0Zm20,36a12,12,0,1,1-12-12A12,12,0,0,1,140,180Z"/>',
    'warning-circle': '<path d="M128,24A104,104,0,1,0,232,128,104.11,104.11,0,0,0,128,24Zm0,192a88,88,0,1,1,88-88A88.1,88.1,0,0,1,128,216Zm-8-80V80a8,8,0,0,1,16,0v56a8,8,0,0,1-16,0Zm20,36a12,12,0,1,1-12-12A12,12,0,0,1,140,172Z"/>',
    'info': '<path d="M128,24A104,104,0,1,0,232,128,104.11,104.11,0,0,0,128,24Zm0,192a88,88,0,1,1,88-88A88.1,88.1,0,0,1,128,216Zm16-40a8,8,0,0,1-8,8,16,16,0,0,1-16-16V128a8,8,0,0,1,0-16,16,16,0,0,1,16,16v40A8,8,0,0,1,144,176ZM112,84a12,12,0,1,1,12,12A12,12,0,0,1,112,84Z"/>',
    'x': '<path d="M205.66,194.34a8,8,0,0,1-11.32,11.32L128,139.31,61.66,205.66a8,8,0,0,1-11.32-11.32L116.69,128,50.34,61.66A8,8,0,0,1,61.66,50.34L128,116.69l66.34-66.35a8,8,0,0,1,11.32,11.32L139.31,128Z"/>',
    'user-circle': '<path d="M128,24A104,104,0,1,0,232,128,104.11,104.11,0,0,0,128,24ZM74.08,197.5a64,64,0,0,1,107.84,0,87.83,87.83,0,0,1-107.84,0ZM96,120a32,32,0,1,1,32,32A32,32,0,0,1,96,120Zm97.76,66.41a79.66,79.66,0,0,0-36.06-28.75,48,48,0,1,0-59.4,0,79.66,79.66,0,0,0-36.06,28.75,88,88,0,1,1,131.52,0Z"/>',
    'magnifying-glass': '<path d="M229.66,218.34l-50.07-50.06a88.11,88.11,0,1,0-11.31,11.31l50.06,50.07a8,8,0,0,0,11.32-11.32ZM40,112a72,72,0,1,1,72,72A72.08,72.08,0,0,1,40,112Z"/>',
    'funnel': '<path d="M230.6,49.53A15.81,15.81,0,0,0,216,40H40A16,16,0,0,0,28.19,66.76l.08.09L96,139.17V216a16,16,0,0,0,24.87,13.32l32-21.34A16,16,0,0,0,160,194.66V139.17l67.74-72.32.08-.09A15.8,15.8,0,0,0,230.6,49.53ZM40,56h0Zm106.18,74.58A8,8,0,0,0,144,136v58.66L112,216V136a8,8,0,0,0-2.16-5.47L40,56H216Z"/>',
    'sliders-horizontal': '<path d="M40,88H73a32,32,0,0,0,62,0h81a8,8,0,0,0,0-16H135a32,32,0,0,0-62,0H40a8,8,0,0,0,0,16Zm64-24A16,16,0,1,1,88,80,16,16,0,0,1,104,64ZM216,168H199a32,32,0,0,0-62,0H40a8,8,0,0,0,0,16h97a32,32,0,0,0,62,0h17a8,8,0,0,0,0-16Zm-48,24a16,16,0,1,1,16-16A16,16,0,0,1,168,192Z"/>',
    'caret-right': '<path d="M181.66,133.66l-80,80a8,8,0,0,1-11.32-11.32L164.69,128,90.34,53.66a8,8,0,0,1,11.32-11.32l80,80A8,8,0,0,1,181.66,133.66Z"/>',
    'caret-down': '<path d="M213.66,101.66l-80,80a8,8,0,0,1-11.32,0l-80-80A8,8,0,0,1,53.66,90.34L128,164.69l74.34-74.35a8,8,0,0,1,11.32,11.32Z"/>',
    'arrow-circle-down': '<path d="M128,24A104,104,0,1,0,232,128,104.11,104.11,0,0,0,128,24Zm0,192a88,88,0,1,1,88-88A88.1,88.1,0,0,1,128,216Zm37.66-85.66a8,8,0,0,1,0,11.32l-32,32a8,8,0,0,1-11.32,0l-32-32a8,8,0,0,1,11.32-11.32L120,148.69V88a8,8,0,0,1,16,0v60.69l18.34-18.35A8,8,0,0,1,165.66,130.34Z"/>',
    'arrow-circle-up': '<path d="M128,24A104,104,0,1,0,232,128,104.11,104.11,0,0,0,128,24Zm0,192a88,88,0,1,1,88-88A88.1,88.1,0,0,1,128,216Zm37.66-101.66a8,8,0,0,1-11.32,11.32L136,107.31V168a8,8,0,0,1-16,0V107.31l-18.34,18.35a8,8,0,0,1-11.32-11.32l32-32a8,8,0,0,1,11.32,0Z"/>',
    'pencil-simple': '<path d="M227.31,73.37,182.63,28.68a16,16,0,0,0-22.63,0L36.69,152A15.86,15.86,0,0,0,32,163.31V208a16,16,0,0,0,16,16H92.69A15.86,15.86,0,0,0,104,219.31L227.31,96a16,16,0,0,0,0-22.63ZM92.69,208H48V163.31l88-88L180.69,120ZM192,108.68,147.31,64l24-24L216,84.68Z"/>',
    'receipt': '<path d="M72,104a8,8,0,0,1,8-8h96a8,8,0,0,1,0,16H80A8,8,0,0,1,72,104Zm8,40h96a8,8,0,0,0,0-16H80a8,8,0,0,0,0,16ZM232,56V208a8,8,0,0,1-11.58,7.15L192,200.94l-28.42,14.21a8,8,0,0,1-7.16,0L128,200.94,99.58,215.15a8,8,0,0,1-7.16,0L64,200.94,35.58,215.15A8,8,0,0,1,24,208V56A16,16,0,0,1,40,40H216A16,16,0,0,1,232,56Zm-16,0H40V195.06l20.42-10.22a8,8,0,0,1,7.16,0L96,199.06l28.42-14.22a8,8,0,0,1,7.16,0L160,199.06l28.42-14.22a8,8,0,0,1,7.16,0L216,195.06Z"/>',
    'bank': '<path d="M24,104H48v64H32a8,8,0,0,0,0,16H224a8,8,0,0,0,0-16H208V104h24a8,8,0,0,0,4.19-14.81l-104-64a8,8,0,0,0-8.38,0l-104,64A8,8,0,0,0,24,104Zm40,0H96v64H64Zm80,0v64H112V104Zm48,64H160V104h32ZM128,41.39,203.74,88H52.26ZM248,208a8,8,0,0,1-8,8H16a8,8,0,0,1,0-16H240A8,8,0,0,1,248,208Z"/>',
    'wallet': '<path d="M216,64H56a8,8,0,0,1,0-16H192a8,8,0,0,0,0-16H56A24,24,0,0,0,32,56V184a24,24,0,0,0,24,24H216a16,16,0,0,0,16-16V80A16,16,0,0,0,216,64Zm0,128H56a8,8,0,0,1-8-8V78.63A23.84,23.84,0,0,0,56,80H216Zm-48-60a12,12,0,1,1,12,12A12,12,0,0,1,168,132Z"/>',
    'coins': '<path d="M184,89.57V84c0-25.08-37.83-44-88-44S8,58.92,8,84v40c0,20.89,26.25,37.49,64,42.46V172c0,25.08,37.83,44,88,44s88-18.92,88-44V132C248,111.3,222.58,94.68,184,89.57ZM232,132c0,13.22-30.79,28-72,28-3.73,0-7.43-.13-11.08-.37C170.49,151.77,184,139,184,124V105.74C213.87,110.19,232,122.27,232,132ZM72,150.25V126.46A183.74,183.74,0,0,0,96,128a183.74,183.74,0,0,0,24-1.54v23.79A163,163,0,0,1,96,152,163,163,0,0,1,72,150.25Zm96-40.32V124c0,8.39-12.41,17.4-32,22.87V123.5C148.91,120.37,159.84,115.71,168,109.93ZM96,56c41.21,0,72,14.78,72,28s-30.79,28-72,28S24,97.22,24,84,54.79,56,96,56ZM24,124V109.93c8.16,5.78,19.09,10.44,32,13.57v23.37C36.41,141.4,24,132.39,24,124Zm64,48v-4.17c2.63.1,5.29.17,8,.17,3.88,0,7.67-.13,11.39-.35A121.92,121.92,0,0,0,120,171.41v23.46C100.41,189.4,88,180.39,88,172Zm48,26.25V174.4a179.48,179.48,0,0,0,24,1.6,183.74,183.74,0,0,0,24-1.54v23.79a165.45,165.45,0,0,1-48,0Zm64-3.38V171.5c12.91-3.13,23.84-7.79,32-13.57V172C232,180.39,219.59,189.4,200,194.87Z"/>',
    'calendar-blank': '<path d="M208,32H184V24a8,8,0,0,0-16,0v8H88V24a8,8,0,0,0-16,0v8H48A16,16,0,0,0,32,48V208a16,16,0,0,0,16,16H208a16,16,0,0,0,16-16V48A16,16,0,0,0,208,32ZM72,48v8a8,8,0,0,0,16,0V48h80v8a8,8,0,0,0,16,0V48h24V80H48V48ZM208,208H48V96H208V208Z"/>',
    'arrows-left-right': '<path d="M213.66,181.66l-32,32a8,8,0,0,1-11.32-11.32L188.69,184H48a8,8,0,0,1,0-16H188.69l-18.35-18.34a8,8,0,0,1,11.32-11.32l32,32A8,8,0,0,1,213.66,181.66Zm-139.32-64a8,8,0,0,0,11.32-11.32L67.31,88H208a8,8,0,0,0,0-16H67.31L85.66,53.66A8,8,0,0,0,74.34,42.34l-32,32a8,8,0,0,0,0,11.32Z"/>',
    'chart-pie-slice': '<path d="M100,116.43a8,8,0,0,0,4-6.93v-72A8,8,0,0,0,93.34,30,104.06,104.06,0,0,0,25.73,147a8,8,0,0,0,4.52,5.81,7.86,7.86,0,0,0,3.35.74,8,8,0,0,0,4-1.07ZM88,49.62v55.26L40.12,132.51C40,131,40,129.48,40,128A88.12,88.12,0,0,1,88,49.62ZM128,24a8,8,0,0,0-8,8v91.82L41.19,169.73a8,8,0,0,0-2.87,11A104,104,0,1,0,128,24Zm0,192a88.47,88.47,0,0,1-71.49-36.68l75.52-44a8,8,0,0,0,4-6.92V40.36A88,88,0,0,1,128,216Z"/>',
    'arrow-square-out': '<path d="M224,104a8,8,0,0,1-16,0V59.32l-66.33,66.34a8,8,0,0,1-11.32-11.32L196.68,48H152a8,8,0,0,1,0-16h64a8,8,0,0,1,8,8Zm-40,24a8,8,0,0,0-8,8v72H48V80h72a8,8,0,0,0,0-16H48A16,16,0,0,0,32,80V208a16,16,0,0,0,16,16H176a16,16,0,0,0,16-16V136A8,8,0,0,0,184,128Z"/>',
    'house': '<path d="M219.31,108.68l-80-80a16,16,0,0,0-22.62,0l-80,80A15.87,15.87,0,0,0,32,120v96a8,8,0,0,0,8,8h64a8,8,0,0,0,8-8V160h32v56a8,8,0,0,0,8,8h64a8,8,0,0,0,8-8V120A15.87,15.87,0,0,0,219.31,108.68ZM208,208H160V152a8,8,0,0,0-8-8H104a8,8,0,0,0-8,8v56H48V120l80-80,80,80Z"/>',   // upstream
    'upload-simple': '<path d="M224,144v64a8,8,0,0,1-8,8H40a8,8,0,0,1-8-8V144a8,8,0,0,1,16,0v56H208V144a8,8,0,0,1,16,0ZM93.66,77.66,120,51.31V144a8,8,0,0,0,16,0V51.31l26.34,26.35a8,8,0,0,0,11.32-11.32l-40-40a8,8,0,0,0-11.32,0l-40,40A8,8,0,0,0,93.66,77.66Z"/>',   // upstream
    'circle-dashed': '<path d="M96.26,37.05A8,8,0,0,1,102,27.29a104.11,104.11,0,0,1,52,0,8,8,0,0,1-2,15.75,8.15,8.15,0,0,1-2-.26,88.09,88.09,0,0,0-44,0A8,8,0,0,1,96.26,37.05ZM53.79,55.14a104.05,104.05,0,0,0-26,45,8,8,0,0,0,15.42,4.27,88,88,0,0,1,22-38.09A8,8,0,0,0,53.79,55.14ZM43.21,151.55a8,8,0,1,0-15.42,4.28,104.12,104.12,0,0,0,26,45,8,8,0,0,0,11.41-11.22A88.14,88.14,0,0,1,43.21,151.55ZM150,213.22a88,88,0,0,1-44,0,8,8,0,1,0-4,15.49,104.11,104.11,0,0,0,52,0,8,8,0,0,0-4-15.49ZM222.65,146a8,8,0,0,0-9.85,5.58,87.91,87.91,0,0,1-22,38.08,8,8,0,1,0,11.42,11.21,104,104,0,0,0,26-45A8,8,0,0,0,222.65,146Zm-9.86-41.54a8,8,0,0,0,15.42-4.28,104,104,0,0,0-26-45,8,8,0,1,0-11.41,11.22A88,88,0,0,1,212.79,104.45Z"/>',   // upstream
    'hourglass': '<path d="M200,75.64V40a16,16,0,0,0-16-16H72A16,16,0,0,0,56,40V76a16.07,16.07,0,0,0,6.4,12.8L114.67,128,62.4,167.2A16.07,16.07,0,0,0,56,180v36a16,16,0,0,0,16,16H184a16,16,0,0,0,16-16V180.36a16.09,16.09,0,0,0-6.35-12.77L141.27,128l52.38-39.6A16.05,16.05,0,0,0,200,75.64ZM184,216H72V180l56-42,56,42.35Zm0-140.36L128,118,72,76V40H184Z"/>',   // upstream
    'sun': '<path d="M120,40V16a8,8,0,0,1,16,0V40a8,8,0,0,1-16,0Zm72,88a64,64,0,1,1-64-64A64.07,64.07,0,0,1,192,128Zm-16,0a48,48,0,1,0-48,48A48.05,48.05,0,0,0,176,128ZM58.34,69.66A8,8,0,0,0,69.66,58.34l-16-16A8,8,0,0,0,42.34,53.66Zm0,116.68-16,16a8,8,0,0,0,11.32,11.32l16-16a8,8,0,0,0-11.32-11.32ZM192,72a8,8,0,0,0,5.66-2.34l16-16a8,8,0,0,0-11.32-11.32l-16,16A8,8,0,0,0,192,72Zm5.66,114.34a8,8,0,0,0-11.32,11.32l16,16a8,8,0,0,0,11.32-11.32ZM48,128a8,8,0,0,0-8-8H16a8,8,0,0,0,0,16H40A8,8,0,0,0,48,128Zm80,80a8,8,0,0,0-8,8v24a8,8,0,0,0,16,0V216A8,8,0,0,0,128,208Zm112-88H216a8,8,0,0,0,0,16h24a8,8,0,0,0,0-16Z"/>',   // upstream
    'moon': '<path d="M233.54,142.23a8,8,0,0,0-8-2,88.08,88.08,0,0,1-109.8-109.8,8,8,0,0,0-10-10,104.84,104.84,0,0,0-52.91,37A104,104,0,0,0,136,224a103.09,103.09,0,0,0,62.52-20.88,104.84,104.84,0,0,0,37-52.91A8,8,0,0,0,233.54,142.23ZM188.9,190.34A88,88,0,0,1,65.66,67.11a89,89,0,0,1,31.4-26A106,106,0,0,0,96,56,104.11,104.11,0,0,0,200,160a106,106,0,0,0,14.92-1.06A89,89,0,0,1,188.9,190.34Z"/>',   // upstream
    'monitor': '<path d="M208,40H48A24,24,0,0,0,24,64V176a24,24,0,0,0,24,24H208a24,24,0,0,0,24-24V64A24,24,0,0,0,208,40Zm8,136a8,8,0,0,1-8,8H48a8,8,0,0,1-8-8V64a8,8,0,0,1,8-8H208a8,8,0,0,1,8,8Zm-48,48a8,8,0,0,1-8,8H96a8,8,0,0,1,0-16h64A8,8,0,0,1,168,224Z"/>',   // upstream
    'clock': '<path d="M128,24A104,104,0,1,0,232,128,104.11,104.11,0,0,0,128,24Zm0,192a88,88,0,1,1,88-88A88.1,88.1,0,0,1,128,216Zm64-88a8,8,0,0,1-8,8H128a8,8,0,0,1-8-8V72a8,8,0,0,1,16,0v48h48A8,8,0,0,1,192,128Z"/>',   // upstream
    'chat': '<path d="M216,48H40A16,16,0,0,0,24,64V224a15.84,15.84,0,0,0,9.25,14.5A16.05,16.05,0,0,0,40,240a15.89,15.89,0,0,0,10.25-3.78l.09-.07L83,208H216a16,16,0,0,0,16-16V64A16,16,0,0,0,216,48ZM40,224h0ZM216,192H80a8,8,0,0,0-5.23,1.95L40,224V64H216Z"/>',   // upstream
    'plus': '<path d="M224,128a8,8,0,0,1-8,8H136v80a8,8,0,0,1-16,0V136H40a8,8,0,0,1,0-16h80V40a8,8,0,0,1,16,0v80h80A8,8,0,0,1,224,128Z"/>',   // upstream
    'trash': '<path d="M216,48H176V40a24,24,0,0,0-24-24H104A24,24,0,0,0,80,40v8H40a8,8,0,0,0,0,16h8V208a16,16,0,0,0,16,16H192a16,16,0,0,0,16-16V64h8a8,8,0,0,0,0-16ZM96,40a8,8,0,0,1,8-8h48a8,8,0,0,1,8,8v8H96Zm96,168H64V64H192ZM112,104v64a8,8,0,0,1-16,0V104a8,8,0,0,1,16,0Zm48,0v64a8,8,0,0,1-16,0V104a8,8,0,0,1,16,0Z"/>',   // upstream
    'gear': '<path d="M128,80a48,48,0,1,0,48,48A48.05,48.05,0,0,0,128,80Zm0,80a32,32,0,1,1,32-32A32,32,0,0,1,128,160Zm88-29.84q.06-2.16,0-4.32l14.92-18.64a8,8,0,0,0,1.48-7.06,107.21,107.21,0,0,0-10.88-26.25,8,8,0,0,0-6-3.93l-23.72-2.64q-1.48-1.56-3-3L186,40.54a8,8,0,0,0-3.94-6,107.71,107.71,0,0,0-26.25-10.87,8,8,0,0,0-7.06,1.49L130.16,40Q128,40,125.84,40L107.2,25.11a8,8,0,0,0-7.06-1.48A107.6,107.6,0,0,0,73.89,34.51a8,8,0,0,0-3.93,6L67.32,64.27q-1.56,1.49-3,3L40.54,70a8,8,0,0,0-6,3.94,107.71,107.71,0,0,0-10.87,26.25,8,8,0,0,0,1.49,7.06L40,125.84Q40,128,40,130.16L25.11,148.8a8,8,0,0,0-1.48,7.06,107.21,107.21,0,0,0,10.88,26.25,8,8,0,0,0,6,3.93l23.72,2.64q1.49,1.56,3,3L70,215.46a8,8,0,0,0,3.94,6,107.71,107.71,0,0,0,26.25,10.87,8,8,0,0,0,7.06-1.49L125.84,216q2.16.06,4.32,0l18.64,14.92a8,8,0,0,0,7.06,1.48,107.21,107.21,0,0,0,26.25-10.88,8,8,0,0,0,3.93-6l2.64-23.72q1.56-1.48,3-3L215.46,186a8,8,0,0,0,6-3.94,107.71,107.71,0,0,0,10.87-26.25,8,8,0,0,0-1.49-7.06Zm-16.1-6.5a73.93,73.93,0,0,1,0,8.68,8,8,0,0,0,1.74,5.48l14.19,17.73a91.57,91.57,0,0,1-6.23,15L187,173.11a8,8,0,0,0-5.1,2.64,74.11,74.11,0,0,1-6.14,6.14,8,8,0,0,0-2.64,5.1l-2.51,22.58a91.32,91.32,0,0,1-15,6.23l-17.74-14.19a8,8,0,0,0-5-1.75h-.48a73.93,73.93,0,0,1-8.68,0,8,8,0,0,0-5.48,1.74L100.45,215.8a91.57,91.57,0,0,1-15-6.23L82.89,187a8,8,0,0,0-2.64-5.1,74.11,74.11,0,0,1-6.14-6.14,8,8,0,0,0-5.1-2.64L46.43,170.6a91.32,91.32,0,0,1-6.23-15l14.19-17.74a8,8,0,0,0,1.74-5.48,73.93,73.93,0,0,1,0-8.68,8,8,0,0,0-1.74-5.48L40.2,100.45a91.57,91.57,0,0,1,6.23-15L69,82.89a8,8,0,0,0,5.1-2.64,74.11,74.11,0,0,1,6.14-6.14A8,8,0,0,0,82.89,69L85.4,46.43a91.32,91.32,0,0,1,15-6.23l17.74,14.19a8,8,0,0,0,5.48,1.74,73.93,73.93,0,0,1,8.68,0,8,8,0,0,0,5.48-1.74L155.55,40.2a91.57,91.57,0,0,1,15,6.23L173.11,69a8,8,0,0,0,2.64,5.1,74.11,74.11,0,0,1,6.14,6.14,8,8,0,0,0,5.1,2.64l22.58,2.51a91.32,91.32,0,0,1,6.23,15l-14.19,17.74A8,8,0,0,0,199.87,123.66Z"/>',   // upstream
    'arrows-clockwise': '<path d="M224,48V96a8,8,0,0,1-8,8H168a8,8,0,0,1,0-16h28.69L182.06,73.37a79.56,79.56,0,0,0-56.13-23.43h-.45A79.52,79.52,0,0,0,69.59,72.71,8,8,0,0,1,58.41,61.27a96,96,0,0,1,135,.79L208,76.69V48a8,8,0,0,1,16,0ZM186.41,183.29a80,80,0,0,1-112.47-.66L59.31,168H88a8,8,0,0,0,0-16H40a8,8,0,0,0-8,8v48a8,8,0,0,0,16,0V179.31l14.63,14.63A95.43,95.43,0,0,0,130,222.06h.53a95.36,95.36,0,0,0,67.07-27.33,8,8,0,0,0-11.18-11.44Z"/>',   // upstream
    'asterisk': '<path d="M214.86,180.12a8,8,0,0,1-11,2.74L136,142.13V216a8,8,0,0,1-16,0V142.13L52.12,182.86a8,8,0,1,1-8.23-13.72L112.45,128,43.89,86.86a8,8,0,1,1,8.23-13.72L120,113.87V40a8,8,0,0,1,16,0v73.87l67.88-40.73a8,8,0,1,1,8.23,13.72L143.55,128l68.56,41.14A8,8,0,0,1,214.86,180.12Z"/>',   // upstream
});
/** An inline icon by name, sized by its class or 1em. */
function icon(name, cls = '') {
    const inner = ICONS[name];
    if (!inner) return '';
    return `<svg class="ph${cls ? ' ' + cls : ''}" viewBox="0 0 256 256" fill="currentColor" aria-hidden="true" focusable="false">${inner}</svg>`;
}

// ---------------------------------------------------------------------------
// State kept per viewer (a convenience only; never a figure)
// ---------------------------------------------------------------------------

const store = {
    get(key, fallback) {
        try { const v = localStorage.getItem('fin.' + key); return v === null ? fallback : JSON.parse(v); }
        catch (_) { return fallback; }
    },
    set(key, value) {
        try { localStorage.setItem('fin.' + key, JSON.stringify(value)); } catch (_) { /* private window */ }
    },
};

const S = {
    month: null,               // the as-at month: the balance sheet's and the period filter's
    sheetSort: { key: null, dir: 'asc' },
    needsLook: false,
    spending: store.get('spending', { book: 'all', view: 'type', span: 'month', search: '', type: '', oneOffs: true, account: '' }),
    changes: { who: 'all', state: 'any', newOnly: false, asked: false, open: new Set() },
    lastSeenMark: null,        // the mark as it was when Changes was opened: the divider sits there
    marks: { rows: {}, accounts: {} },  // what Claude changed since you last looked (the quiet mark)
    manyRows: null,            // the server's count over which a chat change asks first (mcp_tools.MANY_ROWS)
};

function todayIso() {
    const d = new Date();
    return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
}
function currentMonth() { return todayIso().slice(0, 7); }
function prevMonth(m) {
    let [y, n] = m.split('-').map(Number);
    n -= 1; if (n === 0) { n = 12; y -= 1; }
    return `${y}-${String(n).padStart(2, '0')}`;
}
function monthsBack(m, count) {
    const out = [m];
    while (out.length < count) out.unshift(prevMonth(out[0]));
    return out;
}
function monthEnd(m) {
    const [y, n] = m.split('-').map(Number);
    return `${m}-${String(new Date(y, n, 0).getDate()).padStart(2, '0')}`;
}
function sheetMonths() {
    const out = [];
    let m = currentMonth();
    while (m >= SHEET_START) { out.push(m); m = prevMonth(m); }
    return out;
}
S.month = store.get('month', null);
if (!S.month || S.month < SHEET_START || S.month > currentMonth()) S.month = currentMonth();

// ---------------------------------------------------------------------------
// Words and numbers
// ---------------------------------------------------------------------------

function esc(value) {
    return String(value ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

const SIGNS = { SGD: 'S$', INR: '₹', USD: 'US$', EUR: '€', GBP: '£', AUD: 'A$', JPY: '¥', MYR: 'RM', HKD: 'HK$' };
const DIGITS = { JPY: 0, KRW: 0, VND: 0, IDR: 0 };
const formatters = {};
function digitsOf(cur) { return DIGITS[cur] ?? 2; }
function groupFor(cur) {
    const d = digitsOf(cur);
    const locale = cur === 'INR' ? 'en-IN' : 'en-US';   // ruling 6: rupees in lakhs and crores
    const key = locale + d;
    if (!formatters[key]) formatters[key] = new Intl.NumberFormat(locale, { minimumFractionDigits: d, maximumFractionDigits: d });
    return formatters[key];
}
/** An amount in whole minor units, with its currency: "S$ 1,234.50",
 *  "₹ 10,84,000.00", owed "S$ −842,000.00". signed: show + on money in. */
function money(minor, cur = 'SGD', { signed = false } = {}) {
    if (minor === null || minor === undefined || Number.isNaN(minor)) return '—';
    cur = cur || 'SGD';
    const d = digitsOf(cur);
    const sign = minor < 0 ? '−' : (signed && minor > 0 ? '+' : '');
    return `${SIGNS[cur] || cur} ${sign}${groupFor(cur).format(Math.abs(minor) / 10 ** d)}`;
}
function toMinor(amount, cur = 'SGD') {
    if (amount === null || amount === undefined) return null;
    return Math.round(Number(amount) * 10 ** digitsOf(cur));
}
/** Money in or money out, as folio's kind pill: a tinted square with an
 *  arrow, then the word. Never colour alone. */
function dirTag(lane) {
    return lane === 'in'
        ? `<span class="dir in" title="money in"><span class="dir__icon">${icon('arrow-circle-down')}</span>in</span>`
        : `<span class="dir out" title="money out"><span class="dir__icon">${icon('arrow-circle-up')}</span>out</span>`;
}
/** A row's amount as the lists show it: money out and money in each marked. */
function rowAmount(minor, cur) {
    if (minor === null || minor === undefined) return '—';
    if (minor < 0) return `${dirTag('in')} <span class="num">${esc(money(-minor, cur))}</span>`;
    return `${dirTag('out')} <span class="num">${esc(money(minor, cur))}</span>`;
}
const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
const LONG_MONTHS = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December'];
function day(iso, { year = true } = {}) {
    if (!iso) return '—';
    const [y, m, d] = iso.slice(0, 10).split('-').map(Number);
    return `${d} ${MONTHS[m - 1]}${year ? ' ' + y : ''}`;
}
function monthName(m, { short = false } = {}) {
    const [y, n] = m.split('-').map(Number);
    return `${(short ? MONTHS : LONG_MONTHS)[n - 1]} ${y}`;
}
/** A history time (UTC, "YYYY-MM-DD HH:MM:SS") in the viewer's own time. */
function when(utc) {
    if (!utc) return '—';
    const t = new Date(utc.replace(' ', 'T') + 'Z');
    if (Number.isNaN(t.getTime())) return utc;
    const hm = `${String(t.getHours()).padStart(2, '0')}:${String(t.getMinutes()).padStart(2, '0')}`;
    const local = `${t.getFullYear()}-${String(t.getMonth() + 1).padStart(2, '0')}-${String(t.getDate()).padStart(2, '0')}`;
    if (local === todayIso()) return `today ${hm}`;
    return `${t.getDate()} ${MONTHS[t.getMonth()]}, ${hm}`;
}
function daysBetween(a, b) {
    return Math.round((Date.parse(b) - Date.parse(a)) / 86400000);
}
function plural(n, one, many) { return `${n} ${n === 1 ? one : (many || one + 's')}`; }
function pct(part, whole) {
    if (!whole) return '0%';
    const p = (part / whole) * 100;
    if (p > 0 && p < 1) return '<1%';
    return `${Math.round(p)}%`;
}
function sentence(s) { return s ? s.charAt(0).toUpperCase() + s.slice(1) : s; }

// ---------------------------------------------------------------------------
// The API
// ---------------------------------------------------------------------------

const cache = new Map();

async function get(url, { fresh = false } = {}) {
    if (!fresh && cache.has(url)) return cache.get(url);
    const p = fetch(url, { headers: { Accept: 'application/json' } }).then(async res => {
        const data = await res.json().catch(() => ({}));
        if (!res.ok) {
            const err = new Error(data.error || `fin could not read ${url}`);
            err.status = res.status;
            throw err;
        }
        return data;
    });
    cache.set(url, p);
    p.catch(() => cache.delete(url));
    return p;
}

/** A write. Returns {ok, status, data, change}: change is the history entry
 *  it made, for the toast's Undo. Every write clears what was read. */
async function send(method, url, body) {
    const opts = { method, headers: { Accept: 'application/json' } };
    if (body instanceof FormData) opts.body = body;
    else if (body !== undefined) { opts.headers['Content-Type'] = 'application/json'; opts.body = JSON.stringify(body); }
    let res;
    try { res = await fetch(url, opts); }
    catch (_) { return { ok: false, status: 0, data: { error: 'fin could not be reached; nothing was changed' } }; }
    const data = await res.json().catch(() => ({}));
    cache.clear();
    const change = res.headers.get('X-Fin-Change');
    return { ok: res.ok && !data.error, status: res.status, data, change: change ? Number(change) : null };
}

/** A write the operator made: toast it with Undo, or say why it failed. */
async function act(method, url, body, done) {
    const r = await send(method, url, body);
    if (!r.ok) { toast(r.data.error || 'That did not work; nothing was changed', { bad: true }); return null; }
    toast(done || 'Saved', { undo: r.change });
    refreshFrame();
    return r;
}

function sheetFor(month) { return get(`/api/balance-sheet?month=${month}`); }

// A row list read whole, every page of it, so no total is cut short. Past
// MAX_ROW_PAGES pages it stops, and `total` beside `transactions.length`
// lets the page say "showing N of M".
const ROWS_PER_PAGE = 2000;
const MAX_ROW_PAGES = 25;
async function getAllRows(query) {
    const url = page => `/api/transactions?${query}${query ? '&' : ''}per_page=${ROWS_PER_PAGE}&page=${page}`;
    const first = await get(url(1));
    const pages = Math.min(first.pages || 1, MAX_ROW_PAGES);
    const rest = await Promise.all(Array.from({ length: Math.max(0, pages - 1) }, (_, i) => get(url(i + 2))));
    const transactions = first.transactions.concat(...rest.map(r => r.transactions));
    return { transactions, total: first.total ?? transactions.length };
}
function shownOf(list) {
    return list.total > list.transactions.length ? `showing ${list.transactions.length} of ${list.total}` : '';
}

/** The quiet mark: a small dot on a row or balance Claude changed since you
 *  last looked, linking to that change in Changes. */
function claudeMark(entryId) {
    if (!entryId) return '';
    return `<button type="button" class="cmark" data-act="goto-change" data-entry="${esc(entryId)}" title="Changed by Claude since you last looked" aria-label="Changed by Claude since you last looked: see change ${esc(entryId)}"><span aria-hidden="true"></span></button>`;
}
function rowMark(rowId) { return claudeMark(S.marks.rows[rowId]); }
function accountMark(accountId) { return claudeMark(S.marks.accounts[accountId]); }

async function refs() {
    const [types, books, accounts, review, kinds] = await Promise.all([
        get('/api/types'), get('/api/books'), get('/api/accounts'), get('/api/review'), get('/api/account-kinds'),
    ]);
    const typeById = new Map(types.map(t => [t.id, t]));
    const accountById = new Map(accounts.map(a => [a.id, a]));
    return { types, books, accounts, review, kinds, typeById, accountById };
}
async function servicesList() { return get('/api/services'); }

// ---------------------------------------------------------------------------
// Toasts and sheets
// ---------------------------------------------------------------------------

function toast(message, { bad = false, undo = null, ms = 6000 } = {}) {
    const box = document.createElement('div');
    box.className = 'toast' + (bad ? ' bad' : ' ok');
    box.setAttribute('role', bad ? 'alert' : 'status');
    box.innerHTML = `${icon(bad ? 'warning-circle' : 'check-circle')}<span>${esc(message)}</span>`;
    if (undo) {
        const b = document.createElement('button');
        b.type = 'button';
        b.className = 'link';
        b.textContent = 'Undo';
        b.onclick = async () => { box.remove(); await undoEntry(undo); };
        box.appendChild(b);
    }
    $('#toasts').appendChild(box);
    setTimeout(() => box.remove(), undo ? ms + 4000 : ms);
}

let sheetCloser = null;
/** A sheet: a right drawer on the desk, a bottom sheet on a phone (folio's
 *  kit drawer). eyebrow sits over the title; center makes a short confirm;
 *  foot is a row of buttons kept in view under the body. */
function openSheet(title, body, { sub = '', eyebrow = '', center = false, foot = '' } = {}) {
    closeSheet();
    const root = $('#sheets');
    root.innerHTML = `<div class="scrim" data-act="scrim">
        <section class="sheet${center ? ' sheet--center' : ''}" role="dialog" aria-modal="true" aria-labelledby="sheet-title" tabindex="-1">
            <div class="sheet-head"><div>${eyebrow ? `<span class="eyebrow">${eyebrow}</span>` : ''}<h2 id="sheet-title">${title}</h2>${sub ? `<p class="muted small">${sub}</p>` : ''}</div>
            <button type="button" class="close" data-act="close-sheet" aria-label="Close">${icon('x')}</button></div>
            <div class="sheet-body stack">${body}</div>
            ${foot ? `<div class="sheet-foot">${foot}</div>` : ''}
        </section></div>`;
    const sheet = $('.sheet', root);
    const keys = e => { if (e.key === 'Escape') closeSheet(); };
    document.addEventListener('keydown', keys);
    sheetCloser = () => document.removeEventListener('keydown', keys);
    setTimeout(() => (sheet.querySelector('input, select, textarea, .choice, .btn.primary') || sheet).focus(), 30);
    return sheet;
}
function closeSheet() {
    if (sheetCloser) { sheetCloser(); sheetCloser = null; }
    $('#sheets').innerHTML = '';
}
function sheetBody() { return $('#sheets .sheet-body'); }

// ---------------------------------------------------------------------------
// Trust markers: one vocabulary, each a word and a link to its fix
// ---------------------------------------------------------------------------

function tag(kind, text, { href = null, act = null, data = {}, title = '' } = {}) {
    const icons = { ties: 'check-circle', off: 'prohibit', refused: 'prohibit', unexplained: 'warning-circle', stale: 'clock', nofig: 'warning',
        notchecked: 'circle-dashed', aside: 'circle-dashed', asked: 'chat', you: 'user-circle' };
    const glyphs = { claude: '✳', yours: '†' };
    const ic = icons[kind] ? icon(icons[kind]) : glyphs[kind] ? `<span class="ic" aria-hidden="true">${glyphs[kind]}</span>` : '';
    const attrs = Object.entries(data).map(([k, v]) => ` data-${k}="${esc(v)}"`).join('');
    const t = title ? ` title="${esc(title)}"` : '';
    if (href) return `<a class="tag ${kind}" href="${href}"${t}>${ic}${esc(text)}</a>`;
    if (act) return `<button type="button" class="tag ${kind}" data-act="${act}"${attrs}${t}>${ic}${esc(text)}</button>`;
    return `<span class="tag ${kind}"${t}>${ic}${esc(text)}</span>`;
}

/** What a balance-sheet line's balance rests on, as a marker. */
function restsOn(line, { short = false } = {}) {
    const r = line.rests_on;
    if (line.counted_in) return `<span class="muted">${esc(line.balance)}</span>`;
    if (!r) {
        if (line.since_label) return `<span class="muted">worked out from rows ${esc(line.since_label)}</span>`;
        return '<span class="muted">nothing to rest on yet</span>';     // the "no figure" marker sits where the amount would
    }
    const date = day(r.date, { year: !short && !r.date.startsWith(S.month.slice(0, 4)) });
    if (r.source === 'supplied') {
        const stale = r.age_days > STALE_DAYS;
        const age = `${r.age_days} days old`;
        return `your figure ${esc(date)}${r.age_days > 0 ? `, <b>${esc(age)}</b>` : ''} ${stale ? tag('stale', 'stale', { act: 'figure', data: { account: line.account_id }, title: 'Enter a newer figure' }) : ''}`;
    }
    return `statement ${esc(date)}`;
}
/** A balance in another currency with no saved rate for its day: left out of
 *  the S$ totals until a rate is fetched or entered. */
function lacksRate(line) {
    return !!line.currency && line.currency !== 'SGD' && !line.rate && !line.counted_in
        && line.balance_minor !== null && line.balance_minor !== undefined;
}
/** The day a sheet's rates are for: its as-at day, never after today. */
function rateDay(asAt) {
    const d = asAt || monthEnd(S.month);
    return d > todayIso() ? todayIso() : d;
}
function fixFor(line, asAt) {
    if (lacksRate(line)) return { act: 'rate', data: { currency: line.currency, date: rateDay(asAt) }, title: `Fetch or enter the ${line.currency} rate` };
    const acct = window.__accountById?.get(line.account_id);
    if (acct && acct.takes_a_figure) return { act: 'figure', data: { account: line.account_id }, title: 'Enter a figure' };
    return { href: `#/books/account/${line.account_id}`, title: 'See why' };
}
function checkMarker(line) {
    const c = line.check;
    if (!c) return `<span class="muted">${line.rests_on && line.rests_on.source === 'supplied' ? 'no check: your figure is the fact' : 'no check possible'}</span>`;
    const href = `#/books/account/${line.account_id}`;
    if (c.status === 'ties') return tag('ties', 'ties', { href, title: 'See the tie line' });
    if (c.status === 'off') return tag('off', `off by ${money(Math.abs(c.difference_minor), line.currency)}`, { href, title: 'See the tie line' });
    const why = (c.text || '').replace(/^not checked\s*/, '');
    return tag('notchecked', `not checked ${why}`.trim(), { href, title: 'See why' });
}
function tickOf(line) {
    const t = (cls, ic, title) => `<span class="tick ${cls}" title="${title}" role="img" aria-label="${title}">${ic}</span>`;
    if (line.check?.status === 'ties') return t('ties', icon('check-circle'), 'ties');
    if (line.check?.status === 'off') return t('off', icon('prohibit'), 'off by');
    if (line.check?.status === 'not_checked') return t('notchecked', icon('circle-dashed'), 'not checked');
    if (line.rests_on?.source === 'supplied') return t('yours', icon('user-circle'), 'your figure');
    if (line.made_of || line.since_label) return t('rows', '=', 'worked out from rows');
    return '';
}
function lineNeedsLook(line, refusedByAccount) {
    if (line.counted_in) return false;
    if (!line.in_total) return true;
    if (line.check && line.check.status !== 'ties') return true;
    if (line.rests_on?.source === 'supplied' && line.rests_on.age_days > STALE_DAYS) return true;
    return refusedByAccount.has(line.account_id);
}

// ---------------------------------------------------------------------------
// The theme: light, dark, or match my device. The choice is kept per viewer
// (fin-theme); index.html applies it before first paint, this keeps it right.
// ---------------------------------------------------------------------------

const THEME_MQ = window.matchMedia ? window.matchMedia('(prefers-color-scheme: dark)') : null;
const THEME_WORDS = { light: 'Light', dark: 'Dark', system: 'Match my device' };
function themeChoice() {
    let c = 'system';
    try { c = localStorage.getItem('fin-theme') || 'system'; } catch (_) { /* no storage: the device decides */ }
    return THEME_WORDS[c] ? c : 'system';
}
function applyTheme(choice) {
    const dark = choice === 'dark' || (choice === 'system' && !!THEME_MQ && THEME_MQ.matches);
    const r = document.documentElement;
    const was = r.dataset.theme;
    r.dataset.theme = dark ? 'dark' : 'light';
    r.dataset.themeChoice = choice;
    document.getElementById('theme-color')?.setAttribute('content', dark ? '#17130f' : '#f5ede4');
    const btn = $('#theme-btn');
    if (btn) btn.setAttribute('aria-label', `Theme: ${THEME_WORDS[choice]}`);
    $$('#theme-menu [data-theme-choice]').forEach(b => b.setAttribute('aria-checked', String(b.dataset.themeChoice === choice)));
    if (was && was !== r.dataset.theme) document.dispatchEvent(new CustomEvent('fin:theme'));   // charts redraw on this
}
function setTheme(choice) {
    try { localStorage.setItem('fin-theme', choice); } catch (_) { /* kept for this page only */ }
    applyTheme(choice);
}
if (THEME_MQ) {
    const follow = () => { if (themeChoice() === 'system') applyTheme('system'); };
    if (THEME_MQ.addEventListener) THEME_MQ.addEventListener('change', follow); else if (THEME_MQ.addListener) THEME_MQ.addListener(follow);
}
function themeMenu(open, { focus = true } = {}) {
    const btn = $('#theme-btn'), menu = $('#theme-menu');
    if (!btn || !menu) return;
    menu.hidden = !open;
    btn.setAttribute('aria-expanded', String(open));
    if (open) {
        const items = $$('[role="menuitemradio"]', menu);
        (items.find(b => b.getAttribute('aria-checked') === 'true') || items[0]).focus();
    } else if (focus) btn.focus();
}
document.addEventListener('DOMContentLoaded', () => {
    applyTheme(themeChoice());
    const btn = $('#theme-btn'), menu = $('#theme-menu');
    if (!btn || !menu) return;
    btn.addEventListener('click', () => themeMenu(menu.hidden));
    menu.addEventListener('click', e => {
        const b = e.target.closest('[data-theme-choice]');
        if (!b) return;
        setTheme(b.dataset.themeChoice);
        themeMenu(false);
    });
    menu.addEventListener('keydown', e => {
        const items = $$('[role="menuitemradio"]', menu);
        const i = items.indexOf(document.activeElement);
        if (e.key === 'ArrowDown') { e.preventDefault(); items[(i + 1) % items.length].focus(); }
        else if (e.key === 'ArrowUp') { e.preventDefault(); items[(i - 1 + items.length) % items.length].focus(); }
        else if (e.key === 'Home') { e.preventDefault(); items[0].focus(); }
        else if (e.key === 'End') { e.preventDefault(); items[items.length - 1].focus(); }
        else if (e.key === 'Escape') { e.preventDefault(); themeMenu(false); }
        else if (e.key === 'Tab') themeMenu(false, { focus: false });
    });
    document.addEventListener('click', e => { if (!menu.hidden && !e.target.closest('#theme')) themeMenu(false, { focus: false }); });
});

/** Chart colours, read from the tokens when drawing (both themes). */
function chartTokens() {
    const st = getComputedStyle(document.documentElement), v = n => st.getPropertyValue(n).trim();
    return { ink: v('--text-tertiary'), grid: v('--border-subtle'), base: v('--border-emphasis'), mono: v('--font-mono'), surface: v('--bg-surface'),
        current: v('--text-primary'), books: [v('--chart-1'), v('--chart-6'), v('--chart-2'), v('--chart-3')], kin: v('--k-in'), kout: v('--k-out') };
}
// A theme change redraws whatever is drawn from the tokens: the charts.
document.addEventListener('fin:theme', () => { if (typeof charts !== 'undefined' && charts.length) rerender(); });

// ---------------------------------------------------------------------------
// The frame: places, counts, the "Claude may write" pill
// ---------------------------------------------------------------------------

async function refreshFrame() {
    try {
        const [settings, q] = await Promise.all([get('/api/settings', { fresh: true }), loadQueue()]);
        $$('[data-count="claude"]').forEach(el => { el.textContent = settings.claude_count ? settings.claude_count : ''; el.title = `${plural(settings.claude_count, 'change')} by Claude since you last looked`; });
        $$('[data-count="queue"]').forEach(el => { el.textContent = q.count ? q.count : ''; el.title = `${plural(q.count, 'thing')} waiting`; });
        const pill = $('#write-pill');
        pill.classList.toggle('off', !settings.claude_may_write);
        $('[data-write-state]').textContent = settings.claude_may_write ? 'on' : 'off';
    } catch (_) { /* the page itself says what failed */ }
}

const ROUTES = [
    [/^\/?$/, 'home', () => viewHome()],
    [/^\/queue$/, 'queue', () => viewQueue()],
    [/^\/books$/, 'books', () => viewSheet()],
    [/^\/books\/account\/(\d+)$/, 'books', m => viewAccount(Number(m[1]))],
    [/^\/books\/spending$/, 'books', () => viewSpending()],
    [/^\/books\/bills$/, 'books', () => viewBills()],
    [/^\/books\/lists(?:\/(\w+))?$/, 'books', m => viewLists(m[1] || 'merchants')],
    [/^\/books\/import$/, 'books', () => viewImport()],
    [/^\/changes$/, 'changes', () => viewChanges()],
];

let renderToken = 0;
async function render() {
    const path = (location.hash.replace(/^#/, '') || '/').split('?')[0];
    const route = ROUTES.find(([re]) => re.test(path)) || ROUTES[0];
    const match = path.match(route[0]) || [];
    $$('[data-place]').forEach(a => { if (a.dataset.place === route[1]) a.setAttribute('aria-current', 'page'); else a.removeAttribute('aria-current'); });
    const token = ++renderToken;
    const view = $('#view');
    try {
        const [rr, marks] = await Promise.all([refs(), get('/api/changes/marks', { fresh: true }).catch(() => null)]);
        window.__accountById = rr.accountById;
        S.marks = marks || { rows: {}, accounts: {} };
        const html = await route[2](match);
        if (token !== renderToken) return;
        if (typeof html === 'string') view.innerHTML = html;
        if (view.__after) { const after = view.__after; view.__after = null; after(); }
        if (afterNextRender) { const next = afterNextRender; afterNextRender = null; setTimeout(next, 60); }
    } catch (err) {
        if (token !== renderToken) return;
        view.innerHTML = `<div class="notice bad">${icon('warning-circle')}<span>${esc(err.message || 'fin could not show this page')}</span></div>`;
        console.warn(err);
    }
    refreshFrame();
}
function afterRender(fn) { $('#view').__after = fn; }
function rerender() { render(); }

window.addEventListener('hashchange', () => { closeSheet(); render(); window.scrollTo(0, 0); });
document.addEventListener('DOMContentLoaded', render);

// One handler for every [data-act]: the views write plain HTML.
const ACT = {};
document.addEventListener('click', e => {
    const el = e.target.closest('[data-act]');
    if (!el) return;
    const fn = ACT[el.dataset.act];
    if (!fn) return;
    if (el.dataset.act === 'scrim' && e.target !== el) return;
    e.preventDefault();
    fn(el, e);
});
document.addEventListener('change', e => {
    const el = e.target.closest('[data-change]');
    if (el && ACT[el.dataset.change]) ACT[el.dataset.change](el, e);
});
ACT['close-sheet'] = () => closeSheet();
ACT.scrim = () => closeSheet();

function booksNav(current) {
    const items = [['sheet', '#/books', 'Balance sheet', 'scales'], ['spending', '#/books/spending', 'Spending', 'chart-pie-slice'],
        ['bills', '#/books/bills', 'Bills', 'receipt'], ['lists', '#/books/lists', 'Lists', 'list-bullets'], ['import', '#/books/import', 'Import', 'upload-simple']];
    return `<nav class="seg subnav" aria-label="Books">${items.map(([k, href, label, ic]) =>
        `<a href="${href}"${k === current ? ' aria-current="page"' : ''}>${icon(ic)}${label}</a>`).join('')}</nav>`;
}
function monthPicker() {
    return `<label class="field" style="min-width:190px"><span>As at the end of</span>
        <select data-change="month">${sheetMonths().map(m => `<option value="${m}"${m === S.month ? ' selected' : ''}>${monthName(m)}${m === currentMonth() ? ' (so far)' : ''}</option>`).join('')}</select></label>`;
}
ACT.month = el => { S.month = el.value; store.set('month', S.month); rerender(); };

// ---------------------------------------------------------------------------
// What waits: one list behind Home's cards, the Queue and the counts
// ---------------------------------------------------------------------------

function addPeriod(iso, frequency, periods, sign) {
    const [y, m, d] = iso.split('-').map(Number);
    const months = ({ monthly: 1, quarterly: 3, yearly: 12, annual: 12 }[frequency] || 1) * (periods || 1) * sign;
    const t = new Date(y, m - 1 + months, 1);
    const last = new Date(t.getFullYear(), t.getMonth() + 1, 0).getDate();
    t.setDate(Math.min(d, last));
    return `${t.getFullYear()}-${String(t.getMonth() + 1).padStart(2, '0')}-${String(t.getDate()).padStart(2, '0')}`;
}
/** A bill whose last renewal came and went with no payment seen. */
function billMissed(sub) {
    if (sub.status !== 'active' || !sub.computed_renewal) return null;
    const due = addPeriod(sub.computed_renewal, sub.frequency, sub.periods, -1);
    if (daysBetween(due, todayIso()) < BILL_GRACE_DAYS) return null;
    const paid = sub.tx_last_paid || sub.last_paid;
    if (paid && daysBetween(paid, due) <= 5) return null;
    return { due, paid };
}

function rowItem(row, kind) {
    const cur = row.currency || 'SGD';
    const minor = toMinor(row.amount_sgd, cur);
    const lane = minor < 0 ? 'in' : 'out';
    const base = {
        key: `${kind}-${row.id}`, kind, lane, row, currency: cur, amount: Math.abs(minor), signed: minor,
        title: row.description,
        meta: `${day(row.date)} · ${row.account_name || ''}`,
    };
    if (kind === 'transfer') {
        return { ...base, holds: lane === 'out' ? 'Held out of spending until you say what it was' : 'Held out of income until you say what it was',
            actions: [{ label: 'This was…', act: 'this-was', data: { tx: row.id } }] };
    }
    if (kind === 'untyped') {
        return { ...base, holds: lane === 'out' ? 'Counted in spending as “No type”' : 'A refund with no type; counted back as “No type”',
            actions: [{ label: 'This was…', act: 'resolve', data: { tx: row.id } }] };
    }
    // a mixed merchant: its rows take a default type and are looked at each time
    const type = row.display_type || 'no type';
    return { ...base, holds: `${row.service_name || 'A mixed merchant'} sells more than one kind of thing: is ${type} right?`,
        actions: [{ label: `Yes, ${type}`, act: 'mixed-yes', data: { tx: row.id }, primary: true },
            { label: 'Another type', act: 'resolve', data: { tx: row.id, kind: 'mixed' } }] };
}

async function loadQueue() {
    if (cache.has('queue')) return cache.get('queue');
    const p = (async () => {
        const month = currentMonth();
        const [review, untyped, mixed, sheet, refused, subs, r] = await Promise.all([
            getAllRows('flow=review&sort=amount&sort_dir=desc'),
            getAllRows('types=__untyped__&sort=amount&sort_dir=desc'),
            getAllRows('look=mixed&sort=amount&sort_dir=desc'),
            sheetFor(month),
            get('/api/statements/refused'),
            get('/api/subscriptions'),
            refs(),
        ]);
        const items = [];
        // One item per row: a row the server lists twice is shown once, as
        // the first of these it is (waiting for review, no type, mixed).
        const seen = new Set();
        [[review, 'transfer'], [untyped, 'untyped'], [mixed, 'mixed']].forEach(([list, kind]) => list.transactions.forEach(row => {
            if (seen.has(row.id)) return;
            seen.add(row.id);
            items.push(rowItem(row, kind));
        }));
        const capped = [[review, 'transfers waiting'], [untyped, 'rows with no type'], [mixed, 'mixed-merchant rows']]
            .filter(([list]) => list.total > list.transactions.length)
            .map(([list, what]) => `${what}: ${shownOf(list)}`);
        subs.forEach(sub => {
            const missed = billMissed(sub);
            if (!missed) return;
            const cur = sub.currency || 'SGD';
            items.push({
                key: `bill-${sub.id}`, kind: 'bill', lane: 'out', currency: cur, amount: toMinor(sub.amount, cur), sub,
                title: `${sub.service_name || sub.match_pattern}: no payment seen`,
                meta: `due ${day(missed.due)} · last paid ${missed.paid ? day(missed.paid) : 'never seen'}`,
                holds: 'Not in the books: no row matches it since it was due',
                actions: [{ label: 'Paused or stopped it', act: 'bill-pause', data: { sub: sub.id } },
                    { label: 'Open Bills', href: '#/books/bills' }],
            });
        });
        const refusedBy = new Map();
        refused.refused.forEach(f => {
            if (f.account_id) refusedBy.set(f.account_id, f);
            const item = {
                key: `refused-${f.id}`, kind: f.set_aside ? 'aside' : 'refused', lane: f.set_aside ? 'aside' : 'books', refused: f,
                currency: f.currency, amount: Math.abs(f.difference_minor),
                title: `${f.account_name}: the ${day(f.statement_date)} statement was refused`,
                meta: '',
                holds: `None of its ${plural(f.rows, 'row')} is in the books; the balance rests on the last statement that tied and the rows since`,
                actions: f.set_aside
                    ? [{ label: 'Bring it back', act: 'aside', data: { id: f.id, aside: 0 } }, { label: 'See the tie line', act: 'refused-sum', data: { id: f.id } }]
                    : [{ label: 'See the tie line', act: 'refused-sum', data: { id: f.id }, primary: true },
                        { label: 'Known, leave it', act: 'aside', data: { id: f.id, aside: 1 } },
                        { label: 'Import a fixed file', href: '#/books/import', link: true }],
            };
            items.push(item);
        });
        sheet.sections.forEach(section => section.lines.forEach(line => {
            if (line.counted_in) return;
            const acct = r.accountById.get(line.account_id);
            const base = { lane: 'books', currency: line.currency, line };
            if (line.check?.status === 'off') {
                items.push({ ...base, key: `off-${line.account_id}`, kind: 'off', amount: Math.abs(line.check.difference_minor),
                    title: `${line.name} is off by ${money(Math.abs(line.check.difference_minor), line.currency)}`,
                    meta: `statement ${day(line.rests_on?.date)}`,
                    holds: 'Its rows do not carry the earlier statement balance to this one',
                    actions: [{ label: 'See the tie line', href: `#/books/account/${line.account_id}`, primary: true }] });
            }
            if (line.rests_on?.source === 'supplied' && line.rests_on.age_days > STALE_DAYS) {
                items.push({ ...base, key: `stale-${line.account_id}`, kind: 'stale', amount: Math.abs(line.balance_minor ?? 0),
                    title: `${line.name}: your figure is ${line.rests_on.age_days} days old`,
                    meta: `your figure ${day(line.rests_on.date)}`, shown: line.balance_minor,
                    holds: 'Net worth rests on it as it stands',
                    actions: [{ label: 'Enter a figure', act: 'figure', data: { account: line.account_id }, primary: true }] });
            }
            if (!line.in_total && line.balance === 'no figure') {
                const figure = acct && acct.takes_a_figure;
                const aside = refused.refused.find(f => f.set_aside && f.account_id === line.account_id);
                items.push({ ...base, key: `nofig-${line.account_id}`, kind: 'nofig', amount: 0,
                    title: `${line.name} has no figure`,
                    meta: figure ? 'nothing entered yet' : 'no statement balance held',
                    holds: aside ? `Left out of net worth until it has one. Its ${day(aside.statement_date)} statement was refused and you set it aside:`
                        : 'Left out of net worth until it has one',
                    holdsTag: aside ? tag('aside', `refused · set aside, off by ${money(Math.abs(aside.difference_minor), aside.currency)}`,
                        { act: 'refused-sum', data: { id: aside.id }, title: 'See the tie line' }) : '',
                    actions: aside
                        ? [{ label: 'Import a fixed file', href: '#/books/import', primary: true },
                            { label: 'See the tie line', act: 'refused-sum', data: { id: aside.id } },
                            { label: 'Bring it back', act: 'aside', data: { id: aside.id, aside: 0 }, link: true }]
                        : [figure ? { label: 'Enter a figure', act: 'figure', data: { account: line.account_id }, primary: true }
                            : { label: 'Import a statement', href: '#/books/import', primary: true }] });
            }
        }));
        const lanes = { out: [], in: [], books: [], aside: [] };
        items.forEach(i => lanes[i.lane].push(i));
        lanes.out.sort((a, b) => b.amount - a.amount);
        lanes.in.sort((a, b) => b.amount - a.amount);
        const order = { refused: 0, off: 1, stale: 2, nofig: 3 };
        lanes.books.sort((a, b) => order[a.kind] - order[b.kind] || b.amount - a.amount);
        const sum = list => {
            const by = {};
            list.forEach(i => { if (i.kind === 'bill') return; by[i.currency] = (by[i.currency] || 0) + i.amount; });
            return Object.entries(by).map(([c, m]) => money(m, c)).join(' · ') || '';
        };
        return { lanes, refusedBy, capped, count: lanes.out.length + lanes.in.length + lanes.books.length,
            sums: { out: sum(lanes.out.filter(i => i.kind === 'transfer')), in: sum(lanes.in.filter(i => i.kind === 'transfer')) } };
    })();
    cache.set('queue', p);
    p.catch(() => cache.delete('queue'));
    return p;
}

function itemHTML(item) {
    const amount = item.kind === 'nofig' ? '' : item.kind === 'stale'
        ? `<span class="num">${esc(money(item.shown, item.currency))}</span>`
        : item.lane === 'in' ? `${dirTag('in')} <span class="num">${esc(money(item.amount, item.currency))}</span>`
            : item.lane === 'out' ? `${dirTag('out')} <span class="num">${esc(money(item.amount, item.currency))}</span>`
                : '';
    const offBy = item.refused ? tag('off', `off by ${money(Math.abs(item.refused.difference_minor), item.refused.currency)}`,
        { act: 'refused-sum', data: { id: item.refused.id }, title: 'See the tie line' }) : '';
    const marker = { refused: tag('refused', 'refused') + ' ' + offBy, aside: tag('aside', 'refused · set aside') + ' ' + offBy, off: tag('off', 'off by'),
        stale: tag('stale', 'stale'), nofig: tag('nofig', 'no figure'), mixed: tag('notchecked', 'mixed merchant'),
        bill: tag('stale', 'missed bill'), untyped: tag('nofig', 'no type'), transfer: '' }[item.kind] || '';
    const cls = a => a.link ? 'link' : `btn sm${a.primary ? ' ink' : ''}`;
    const actions = (item.actions || []).map(a => a.href
        ? `<a class="${cls(a)}" href="${a.href}">${esc(a.label)}</a>`
        : `<button type="button" class="${cls(a)}" data-act="${a.act}"${Object.entries(a.data || {}).map(([k, v]) => ` data-${k}="${esc(v)}"`).join('')}>${esc(a.label)}</button>`).join('');
    return `<article class="item ${item.kind}">
        <div><div class="what">${esc(item.title)} ${marker}${item.row ? rowMark(item.row.id) : item.line ? accountMark(item.line.account_id) : ''}</div><div class="meta">${esc(item.meta || '')}</div>${item.holds ? `<div class="holds">${esc(item.holds)}${item.holdsTag ? ' ' + item.holdsTag : ''}</div>` : ''}</div>
        <div class="amt">${amount}</div>
        <div class="act">${actions}</div></article>`;
}

// ---------------------------------------------------------------------------
// Home
// ---------------------------------------------------------------------------

function heroFigure(minor, cur) {
    const text = money(minor, cur);
    const m = text.match(/^(\S+)\s(.*?)(\.\d+)?$/);
    if (!m) return esc(text);
    return `<span class="cur">${esc(m[1])}</span>${esc(m[2])}${m[3] ? `<span class="cents">${esc(m[3])}</span>` : ''}`;
}

/** What net worth rests on: every figure in it by its size, owned and owed. */
function restsOnParts(sheet) {
    const parts = { ties: [0, 0], off: [0, 0], notchecked: [0, 0], recent: [0, 0], stale: [0, 0], rows: [0, 0] };
    const leftOut = [];
    sheet.sections.forEach(s => s.lines.forEach(line => {
        if (line.counted_in) return;
        if (!line.in_total) { leftOut.push(line); return; }
        const w = Math.abs(line.value_minor || 0);
        let k;
        if (!line.rests_on) k = 'rows';
        else if (line.rests_on.source === 'supplied') k = line.rests_on.age_days > STALE_DAYS ? 'stale' : 'recent';
        else k = line.check?.status === 'ties' ? 'ties' : line.check?.status === 'off' ? 'off' : 'notchecked';
        parts[k][0] += w; parts[k][1] += 1;
    }));
    const whole = Object.values(parts).reduce((a, [w]) => a + w, 0);
    return { parts, whole, leftOut };
}
const REST_LABELS = {
    ties: 'statement that ties', off: 'statement off by', notchecked: 'statement not checked',
    recent: `your figure, ${STALE_DAYS} days old or newer`, stale: `your figure, over ${STALE_DAYS} days old`, rows: 'worked out from rows (companies, people)',
};
function restsOnHTML(sheet) {
    const { parts, whole, leftOut } = restsOnParts(sheet);
    const keys = Object.keys(parts).filter(k => parts[k][1]);
    const bar = keys.map(k => {
        const share = parts[k][0] / (whole || 1);
        return `<span class="sw-${k}" style="--seg:${(share * 1000).toFixed(1)}" title="${esc(REST_LABELS[k])}: ${pct(parts[k][0], whole)}">${share >= 0.08 ? `<b>${pct(parts[k][0], whole)}</b>` : ''}</span>`;
    }).join('');
    const legend = keys.map(k => `<a href="#/books" data-act="needs-look-link" data-key="${k}"><span class="sw sw-${k}"></span><span><b>${pct(parts[k][0], whole)}</b> ${esc(REST_LABELS[k])} · ${parts[k][1]}</span></a>`).join('');
    const stale = parts.stale[0];
    const words = stale
        ? `<b>${pct(stale, whole)}</b> of what net worth rests on is your own figure over ${STALE_DAYS} days old. Statements that tie hold <b>${pct(parts.ties[0], whole)}</b>.`
        : `Statements that tie hold <b>${pct(parts.ties[0], whole)}</b> of what net worth rests on.`;
    return `<div class="rests"><div class="spread"><span class="eyebrow">What it rests on</span><span class="muted small">each figure by its size, owed included</span></div>
        <div class="rests-bar" role="img" aria-label="What net worth rests on">${bar}</div>
        <div class="legend">${legend}</div>
        <p class="small" style="margin-top:10px">${words}${leftOut.length ? ` ${plural(leftOut.length, 'line is', 'lines are')} left out: ${leftOut.map(l => esc(l.name)).join(', ')}.` : ''}</p></div>`;
}

// Home's own pieces: the Claude line, the net worth hero, what it rests on,
// the month check, what waits (folio's attention items) and the book tiles.

/** When you last looked, as the line says it: "at 20:57 today", "on 3 Oct, 20:57". */
function lookedWhen(utc) {
    if (!utc) return '';
    const w = when(utc);
    return w.startsWith('today ') ? `at ${w.slice(6)} today` : `on ${w}`;
}
/** A machine date inside a server sentence, written out: 2026-08-31 → 31 Aug 2026. */
function plainDates(text) {
    return String(text || '').replace(/\b(\d{4}-\d{2}-\d{2})\b/g, (_, iso) => day(iso));
}

function homeClaudeLine(settings) {
    const looked = lookedWhen(settings.last_looked_at);
    if (settings.claude_count) {
        return `<div class="notice claude home-claude"><span class="home-claude__mark" aria-hidden="true">✳</span>
            <p class="home-claude__text"><b>${plural(settings.claude_count, 'change')} by Claude</b> since you last looked${looked ? ` ${esc(looked)}` : ''}${settings.latest_claude_at ? ` · newest ${esc(when(settings.latest_claude_at).replace(/^today /, ''))}` : ''}</p>
            <div class="home-claude__act"><a class="btn sm" href="#/changes">Check them</a>
            <button type="button" class="btn sm quiet" data-act="looks-right" data-upto="${esc(settings.newest)}">Looks right</button></div></div>`;
    }
    return `<div class="notice home-claude is-quiet"><span class="home-claude__mark" aria-hidden="true">✳</span>
        <p class="home-claude__text">Nothing new from Claude since you last looked${looked ? ` ${esc(looked)}` : ''}.</p>
        <div class="home-claude__act"><a class="link" href="#/changes">Recent changes</a></div></div>`;
}

/** What net worth rests on, Home's telling: short legend words that never
 *  break, and the old figures named, each opening Enter a figure. */
const HOME_REST_LABELS = { ties: 'statement ties', off: 'statement off', notchecked: 'not checked',
    recent: 'your figure, new', stale: 'your figure, old', rows: 'from rows' };
function homeRestsHTML(sheet) {
    const { parts, whole, leftOut } = restsOnParts(sheet);
    const keys = Object.keys(parts).filter(k => parts[k][1]);
    const bar = keys.map(k => {
        const share = parts[k][0] / (whole || 1);
        return `<span class="sw-${k}" style="--seg:${(share * 1000).toFixed(1)}" title="${esc(REST_LABELS[k])}: ${pct(parts[k][0], whole)}">${share >= 0.08 ? `<b>${pct(parts[k][0], whole)}</b>` : ''}</span>`;
    }).join('');
    const legend = keys.map(k => `<a href="#/books" data-act="needs-look-link" title="${esc(sentence(REST_LABELS[k]))}"><span class="sw sw-${k}" aria-hidden="true"></span><span><b>${pct(parts[k][0], whole)}</b> ${esc(HOME_REST_LABELS[k])} <span class="home-rests__n">· ${parts[k][1]}</span></span></a>`).join('');
    const old = sheet.sections.flatMap(s => s.lines)
        .filter(l => !l.counted_in && l.in_total && l.rests_on?.source === 'supplied' && l.rests_on.age_days > STALE_DAYS)
        .sort((a, b) => b.rests_on.age_days - a.rests_on.age_days);
    const stale = parts.stale[0];
    const words = stale
        ? `<b>${pct(stale, whole)}</b> rests on your own figures over ${STALE_DAYS} days old. Each opens Enter a figure. Statements that tie hold <b>${pct(parts.ties[0], whole)}</b>.`
        : `Statements that tie hold <b>${pct(parts.ties[0], whole)}</b> of what net worth rests on.`;
    const oldTags = old.length ? `<div class="home-rests__old">${old.map(l => tag('stale', `${l.name}, ${l.rests_on.age_days} days`,
        { act: 'figure', data: { account: l.account_id }, title: `Enter a figure for ${l.name}` })).join('')}</div>` : '';
    return `<div class="home-rests"><div class="home-rests__head"><span class="eyebrow">What it rests on</span><span class="small muted">each figure by its size, owed included</span></div>
        <div class="rests-bar" role="img" aria-label="What net worth rests on: ${keys.map(k => `${pct(parts[k][0], whole)} ${HOME_REST_LABELS[k]}`).join(', ')}">${bar}</div>
        <div class="legend home-rests__legend">${legend}</div>
        <div class="home-rests__more"><p class="home-rests__words">${words}${leftOut.length ? ` Left out: ${leftOut.map(l => esc(l.name)).join(', ')}.` : ''}</p>${oldTags}</div>
        ${stale ? `<details class="home-rests__fold"><summary><b>${pct(stale, whole)}</b> rests on ${plural(old.length, 'old figure')}: which ${icon('caret-down')}</summary>
            <p class="home-rests__words">${words}${leftOut.length ? ` Left out: ${leftOut.map(l => esc(l.name)).join(', ')}.` : ''}</p>${oldTags}</details>` : ''}</div>`;
}

function homeHero(now, before, prev) {
    let since = '';
    if (before) {
        const change = now.net_worth_minor - before.net_worth_minor;
        const on = esc(day(before.as_at, { year: false }));
        since = change === 0
            ? `<p class="home-hero__since">No change since ${on}</p>`
            : `<p class="home-hero__since"><b class="num">${esc(money(change, 'SGD', { signed: true }))}</b> since ${on}</p>`;
    }
    const chips = [];
    const mc = before && before.month_check;
    if (mc && mc.available && mc.unexplained_minor !== null && mc.unexplained_minor !== undefined) {
        chips.push(mc.unexplained_minor === 0
            ? `<a class="delta-chip delta-chip--up" href="#/books" data-act="go-month-check" data-month="${prev}">${icon('check-circle')}${esc(monthName(prev, { short: true }))} adds up</a>`
            : `<a class="delta-chip delta-chip--down" href="#/books" data-act="go-month-check" data-month="${prev}">${icon('warning-circle')}<span class="num">${esc(money(mc.unexplained_minor, 'SGD'))}</span> unexplained, ${esc(MONTHS[Number(prev.slice(5)) - 1])}</a>`);
    }
    if (now.left_out.length === 1) {
        const l = now.left_out[0];
        const line = now.sections.flatMap(s => s.lines).find(x => x.name === l.name);
        const fix = line ? fixFor(line, now.as_at) : null;
        const attrs = fix?.act ? ` data-act="${fix.act}"${Object.entries(fix.data || {}).map(([k, v]) => ` data-${k}="${esc(v)}"`).join('')}` : '';
        chips.push(`<a class="delta-chip delta-chip--warn" href="${fix?.href || '#/books'}"${attrs} title="${esc(fix?.title || 'See why')}">${icon('warning')}${esc(l.name)}: ${esc(l.why)}, left out</a>`);
    } else if (now.left_out.length > 1) {
        const whys = [...new Set(now.left_out.map(l => l.why))];
        chips.push(`<a class="delta-chip delta-chip--warn" href="#/books" data-act="needs-look-link" title="${esc(now.left_out.map(l => `${l.name}: ${l.why}`).join('; '))}">${icon('warning')}<span>${now.left_out.length} left out${whys.length === 1 ? `<span class="home-hero__why">, ${esc(whys[0])}</span>` : ''}</span></a>`);
    }
    return `<section class="hero-card home-hero">
        <div class="home-hero__head"><span class="eyebrow">Net worth · today</span><a class="link" href="#/books">Balance sheet</a></div>
        <div class="hero-figure">${heroFigure(now.net_worth_minor, now.currency)}</div>
        ${since}
        ${chips.length ? `<div class="chip-row home-hero__chips">${chips.join('')}</div>` : ''}
        ${homeRestsHTML(now)}</section>`;
}

/** The month check beside net worth: the sum drawn, the gap as a chip, and
 *  each reason ending in its fix. */
function monthCheckMini(sheet) {
    const mc = sheet.month_check;
    if (!mc) return '';
    const name = monthName(sheet.month);
    const head = `<div class="home-mc__head"><span class="eyebrow">Month check · ${esc(name)}</span>
        <button type="button" class="link" data-act="go-month-check" data-month="${sheet.month}">Open it</button></div>`;
    if (!mc.available || mc.unexplained_minor === null) {
        return `<section class="card home-mc">${head}<p class="card-empty">${esc(plainDates(mc.why) || 'Not worked out for this month.')}</p></section>`;
    }
    const m = v => esc(money(v, 'SGD'));
    const line = (op, label, minor, cls = '') => `<tr${cls ? ` class="${cls}"` : ''}><td class="op">${op}</td><td>${label}</td><td class="r">${m(minor)}</td></tr>`;
    const sum = `<table class="sum home-mc__sum"><tbody>
        ${line('', `Start, ${esc(day(mc.from, { year: false }))}`, mc.opening_minor)}
        ${line('+', 'income', mc.income_minor)}
        ${line('−', 'spending', mc.spending_minor)}
        ${line('+', 'currency change', mc.currency_change_minor)}
        ${mc.outside_minor ? line(mc.outside_minor < 0 ? '−' : '+', 'moved to or from accounts left out', Math.abs(mc.outside_minor)) : ''}
        ${line('=', 'expected', mc.expected_minor, 'eq')}
        ${line('', `actual, ${esc(day(mc.to, { year: false }))}`, mc.actual_minor)}</tbody></table>`;
    const gap = mc.unexplained_minor === 0
        ? `<span class="delta-chip delta-chip--up">${icon('check-circle')}It adds up</span>`
        : `<span class="delta-chip delta-chip--down">${icon('warning-circle')}<span class="num">${m(mc.unexplained_minor)}</span> unexplained</span>`;
    const lines = sheet.sections.flatMap(s => s.lines);
    const fixLink = fix => fix.href
        ? `<a class="link" href="${fix.href}">${esc(fix.label)}</a>`
        : `<button type="button" class="link" data-act="${fix.act}"${Object.entries(fix.data || {}).map(([k, v]) => ` data-${k}="${esc(v)}"`).join('')}>${esc(fix.label)}</button>`;
    const reasons = [];
    if (mc.review && mc.review.count) {
        reasons.push([`${mc.review.out_count} out (${m(mc.review.out_minor)}) and ${mc.review.in_count} in (${m(mc.review.in_minor)}) wait for a label`, { href: '#/queue', label: 'Label them' }]);
    }
    (mc.not_tying || []).forEach(l => {
        const text = plainDates((l.text || '').replace(/^not checked\s*\((.*)\)$/, '$1'));
        const fix = /no statement balance/.test(l.text || '') ? { href: '#/books/import', label: 'Import a statement' }
            : l.status === 'off' ? { href: `#/books/account/${l.account_id}`, label: 'See the tie line' }
                : { href: `#/books/account/${l.account_id}`, label: 'See the account' };
        reasons.push([`${esc(l.name)}: ${esc(text)}`, fix]);
    });
    (mc.left_out || []).forEach(l => {
        const why = /^no balance on (\d{4}-\d{2}-\d{2})$/.test(l.why) ? `no figure at ${day(l.why.slice(-10))}` : plainDates(l.why);
        const ln = lines.find(x => x.name === l.name);
        const f = ln ? fixFor(ln, sheet.as_at) : { href: '#/books', title: 'See why' };
        reasons.push([`${esc(l.name)} left out: ${esc(why)}`, { ...f, label: f.title === 'See why' ? 'See the account' : f.title }]);
    });
    return `<section class="card home-mc">${head}
        ${sum}
        <div class="home-mc__gap"><span class="label">Unexplained</span>${gap}</div>
        ${reasons.length ? `<ul class="home-mc__reasons">${reasons.map(([text, fix]) => `<li>${icon('warning-circle')}<span>${text} ${fixLink(fix)}</span></li>`).join('')}</ul>` : ''}</section>`;
}

/** One waiting thing, as folio's attention item: an icon tile, the title and
 *  its marker, what it holds out, the amount, and its actions (one ink at
 *  most; accepting fin's guess is camel; setting aside is quiet). */
const HOME_ITEM_ICONS = { transfer: 'arrows-left-right', untyped: 'warning', mixed: 'list-bullets', bill: 'receipt',
    refused: 'prohibit', off: 'prohibit', stale: 'clock', nofig: 'warning', aside: 'circle-dashed' };
function homeItemHTML(item, guesses) {
    const amount = item.kind === 'nofig' ? '' : item.kind === 'stale'
        ? `<span class="num${item.shown < 0 ? ' neg' : ''}">${esc(money(item.shown, item.currency))}</span>`
        : item.lane === 'in' || item.lane === 'out'
            ? `${dirTag(item.lane)} <span class="num">${esc(money(item.amount, item.currency))}</span>`
            : item.kind === 'refused' ? `<span class="num">off by ${esc(money(item.amount, item.currency))}</span>` : '';
    const marker = { refused: tag('refused', 'refused', { act: 'refused-sum', data: { id: item.refused?.id }, title: 'See the tie line' }),
        off: tag('off', 'off by', item.line ? { href: `#/books/account/${item.line.account_id}`, title: 'See the tie line' } : {}),
        stale: tag('stale', 'stale'), nofig: tag('nofig', 'no figure'), mixed: tag('notchecked', 'mixed merchant'),
        bill: tag('stale', 'missed bill'), untyped: tag('nofig', 'no type') }[item.kind] || '';
    const btn = (label, cls, act, data = {}) => `<button type="button" class="btn sm${cls ? ' ' + cls : ''}" data-act="${act}"${Object.entries(data).map(([k, v]) => ` data-${k}="${esc(v)}"`).join('')}>${esc(label)}</button>`;
    const link = (label, cls, href) => `<a class="btn sm${cls ? ' ' + cls : ''}" href="${href}">${esc(label)}</a>`;
    let actions;
    let guessLine = '';
    const guess = item.row && guesses ? guesses.get(item.row.id) : null;
    if (item.kind === 'transfer') actions = btn('This was…', '', 'this-was', { tx: item.row.id });
    else if (item.kind === 'untyped' && guess) {
        guessLine = `<p class="att__guess">fin’s guess: <b>${esc(guess.name)}</b>, ${Math.round(guess.probability * 100)}% sure</p>`;
        actions = btn(`Yes, ${guess.name}`, 'primary', 'home-guess-yes', { tx: item.row.id, type: guess.type_id, merchant: guess.merchant || '' })
            + btn('Something else', '', 'resolve', { tx: item.row.id });
    } else if (item.kind === 'untyped') actions = btn('This was…', '', 'resolve', { tx: item.row.id });
    else if (item.kind === 'mixed') actions = btn(`Yes, ${item.row.display_type || 'no type'}`, 'primary', 'mixed-yes', { tx: item.row.id }) + btn('Another type', '', 'resolve', { tx: item.row.id });
    else if (item.kind === 'refused') actions = btn('See the tie line', 'ink', 'refused-sum', { id: item.refused.id })
        + link('Import a fixed file', '', '#/books/import') + btn('Known, leave it', 'quiet', 'aside', { id: item.refused.id, aside: 1 });
    else actions = (item.actions || []).map(a => a.href ? link(a.label, a.primary ? 'ink' : '', a.href) : btn(a.label, a.primary ? 'ink' : '', a.act, a.data)).join('');
    const mark = item.row ? rowMark(item.row.id) : item.line ? accountMark(item.line.account_id) : '';
    return `<article class="att att--${item.kind}${item.kind === 'refused' ? ' is-blocking' : ''}">
        <span class="att__ic" aria-hidden="true">${icon(HOME_ITEM_ICONS[item.kind] || 'info')}</span>
        <div class="att__main"><p class="att__title">${esc(item.title)} ${marker}${mark}</p>
            <p class="att__meta">${esc(item.meta || '')}</p>${guessLine}${item.holds ? `<p class="att__holds">${esc(item.holds)}</p>` : ''}</div>
        ${amount ? `<div class="att__amt">${amount}</div>` : ''}
        <div class="att__act">${actions}</div></article>`;
}

/** What a lane holds, said kind by kind: "5 transfers S$ 12,200.00 · 84 rows with no type S$ 6,477.50 · 7 bills not seen". */
function laneSummary(list) {
    const kinds = [['transfer', 'transfer', 'transfers', true], ['untyped', 'row with no type', 'rows with no type', true],
        ['mixed', 'mixed-merchant row', 'mixed-merchant rows', true], ['bill', 'bill not seen', 'bills not seen', false],
        ['refused', 'refused statement', 'refused statements', false], ['off', 'statement off', 'statements off', false],
        ['stale', 'old figure', 'old figures', false], ['nofig', 'line with no figure', 'lines with no figure', false]];
    return kinds.map(([k, one, many, summed]) => {
        const of = list.filter(i => i.kind === k);
        if (!of.length) return '';
        const by = {};
        of.forEach(i => { by[i.currency] = (by[i.currency] || 0) + (i.amount || 0); });
        const sum = summed ? ` <b class="num">${Object.entries(by).map(([c, v]) => esc(money(v, c))).join(' · ')}</b>` : '';
        return `<span>${of.length === 1 ? `1 ${one}` : `${of.length} ${many}`}${sum}</span>`;
    }).filter(Boolean).join('<span class="sep" aria-hidden="true">·</span>');
}

async function homeGuesses(items) {
    const rows = items.filter(i => i.kind === 'untyped' && i.row).map(i => i.row.id);
    const out = new Map();
    await Promise.all(rows.map(async id => {
        try {
            const s = await get(`/api/transactions/${id}/suggestion`);
            if (s && (s.route === 'prefill' || s.route === 'top3') && s.types && s.types.length) out.set(id, { ...s.types[0], merchant: s.merchant });
        } catch (_) { /* no guess: the card says "This was…" */ }
    }));
    return out;
}

/** A book's twelve months as small bars, on its own scale; the tile's month dark. */
function homeBars(values, months, current, cls) {
    const top = Math.max(...values.map(v => Math.abs(v)), 0);
    return `<div class="home-bars ${cls}" role="img" aria-label="Twelve months, each on this book's own scale">${values.map((v, i) => {
        const h = top ? Math.max(2, Math.round(Math.abs(v) / top * 100)) : 2;
        return `<span class="${months[i] === current ? 'is-current' : ''}${v ? '' : ' is-zero'}" style="--h:${h}%" title="${esc(monthName(months[i], { short: true }))}"></span>`;
    }).join('')}</div>`;
}
function homeVsAverage(minor, avgMinor, months) {
    const span = `over ${plural(months, 'month')} with rows before it`;
    if (!avgMinor && !minor) return `<span class="home-vs" title="${span}">same as its recent average</span>`;
    if (!avgMinor) return `<span class="home-vs">no recent average to compare</span>`;
    const p = Math.round((minor - avgMinor) / Math.abs(avgMinor) * 100);
    if (p === 0) return `<span class="home-vs" title="${span}">same as its recent average ${esc(money(avgMinor, 'SGD'))}</span>`;
    return `<span class="home-vs" title="The recent average is ${span}"><span aria-hidden="true">${p > 0 ? '▲' : '▼'}</span>${Math.abs(p)}% ${p > 0 ? 'above' : 'below'} its recent average ${esc(money(avgMinor, 'SGD'))}</span>`;
}

async function viewHome() {
    const month = currentMonth();
    const prev = prevMonth(month);
    const [settings, now, before, q, cards, r] = await Promise.all([
        get('/api/settings'), sheetFor(month),
        prev >= SHEET_START ? sheetFor(prev).catch(() => null) : Promise.resolve(null),
        loadQueue(), get(`/api/dashboard/stat-cards?ref_month=${prev}&history=12`), refs(),
    ]);
    const shown = { out: q.lanes.out.slice(0, 3), in: q.lanes.in.slice(0, 3), books: q.lanes.books.slice(0, 3) };
    const guesses = await homeGuesses([...shown.out, ...shown.in]);

    const lane = (key, title, list) => `<div class="home-lane home-lane--${key}">
        <div class="home-lane__head"><h3>${key === 'out' || key === 'in' ? dirTag(key) : ''}${title}</h3>
            <p class="home-lane__sum">${list.length ? laneSummary(list) : ''}</p></div>
        ${list.length ? shown[key].map(i => homeItemHTML(i, guesses)).join('') : `<p class="card-empty">${icon('check-circle')}Nothing waits here.</p>`}
        ${list.length > 3 ? `<a class="link home-lane__more" href="#/queue">${list.length - 3} more in the queue</a>` : ''}</div>`;
    const waits = `<section class="card home-waits">
        <div class="section-header"><h2>Waiting for you</h2><a class="btn sm" href="#/queue">Open the queue <span class="count">${q.count}</span></a></div>
        ${q.capped.length ? `<div class="notice warn">${icon('warning')}<span>Too many to read at once, so these counts and sums are short: ${esc(q.capped.join('; '))}.</span></div>` : ''}
        ${q.count ? `<div class="home-waits__cols">${lane('out', 'Money out', q.lanes.out)}${lane('in', 'Money in', q.lanes.in)}</div>
        ${q.lanes.books.length ? lane('books', 'Figures and statements', q.lanes.books) : ''}`
        : `<p class="home-empty">${icon('check-circle')}Nothing waits for you. Every statement ties.</p>`}</section>`;

    // The book tiles: the month the check beside them covers, each its own
    // figure with its own twelve months; never added.
    const hist = cards.history || [];
    const months = hist.map(h => h.month);
    const tile = ({ cls, eyebrow, label, minor, avg, body, values, href, act, book, foot }) => `<a class="home-tile ${cls}" href="${href}"${act ? ` data-act="${act}" data-book="${esc(book)}" data-month="${esc(cards.ref_month)}"` : ''}>
        <span class="home-tile__eyebrow">${esc(eyebrow)}</span>
        <span class="home-tile__label">${esc(label)}</span>
        <span class="home-tile__fig num">${esc(money(minor, 'SGD'))}</span>
        ${avg}
        <span class="home-tile__body">${body}</span>
        ${values.length ? homeBars(values, months, cards.ref_month, cls) : ''}
        <span class="home-tile__foot">${esc(foot)} ${icon('caret-right')}</span></a>`;
    const tiles = [];
    // Money out is the figure, as Spending shows it; refunds sit beside it,
    // never taken off. A month with no statement in is marked, not a zero.
    r.books.forEach((b, i) => {
        const key = b.name.toLowerCase();
        if (!(key in cards)) return;
        const minor = toMinor(cards[`${key}_out`] ?? cards[key] ?? 0);
        const values = hist.map(h => toMinor(h[`out_${key}`] ?? h[key] ?? 0));
        const backMinor = toMinor(cards[`${key}_back`] || 0);
        const back = backMinor ? `<span class="delta-chip delta-chip--up">${icon('arrow-circle-down')}back ${esc(money(backMinor, 'SGD'))}</span> in refunds, not taken off` : '';
        const missing = cards[`${key}_missing`];
        const avg = missing
            ? `<span class="tag stale">${icon('warning')}no statement for ${esc(monthName(cards.ref_month, { short: true }))}</span>`
            : homeVsAverage(minor, toMinor(cards[`avg_${key}_out`] ?? cards[`avg_${key}`] ?? 0), cards.avg_months || 3);
        const go = missing ? { href: '#/books/import', foot: 'Import the statement' } : { act: 'tile-book', book: b.name, href: '#/books/spending', foot: 'See its rows' };
        const outRows = cards[`${key}_out_rows`] ?? (i === 0 ? cards.household_rows : 0) ?? 0;
        if (i === 0) {
            tiles.push(tile({ cls: 'is-household', eyebrow: 'Household', label: `${b.name} spending`, minor, avg, values, ...go,
                body: `${plural(outRows, 'row')} out${back ? ` · ${back}` : ''}. Held out until labelled: out <b>${esc(money(toMinor(cards.held_out_out_total), 'SGD'))}</b> · in <b>${esc(money(toMinor(cards.held_out_in_total), 'SGD'))}</b>.` }));
        } else {
            tiles.push(tile({ cls: 'is-company', eyebrow: 'Company book', label: `${b.name}, its costs`, minor, avg, values, ...go,
                body: `${back ? `Money out · ${back}. ` : ''}Paid from our accounts. Not household spending; it moves ${esc(b.name)}’s company balance.` }));
        }
    });
    const loanValues = hist.map(h => toMinor(h.loan_principal || 0));
    tiles.push(tile({ cls: 'is-movement', eyebrow: 'Movement, not spending', label: 'Loan principal repaid', minor: toMinor(cards.loan_principal), values: loanValues, href: '#/books', foot: 'See the loans',
        avg: toMinor(cards.loan_instalments) ? '' : '<span class="home-vs">no instalment seen this month</span>',
        body: `What the loans fell by: instalments ${esc(money(toMinor(cards.loan_instalments), 'SGD'))} less interest ${esc(money(toMinor(cards.loan_interest), 'SGD'))}. Still the household’s money.` }));
    const tilesCard = `<section class="card home-tiles">
        <div class="section-header"><h2>${esc(monthName(cards.ref_month))}: ${tiles.length} figures, never added</h2><a class="link" href="#/books/spending">Spending</a></div>
        <div class="home-tiles__grid">${tiles.join('')}</div>
        <p class="card-foot">Each book is its own figure, drawn three different ways so they never read as one sum. Each small chart has its own scale. No total is shown between them.</p></section>`;

    const mcCard = before ? monthCheckMini(before) : '';
    return `<div class="home">${homeClaudeLine(settings)}${homeHero(now, before, prev)}${mcCard}${waits}${tilesCard}</div>`;
}

ACT['tile-book'] = el => {
    S.spending.book = el.dataset.book; S.spending.span = 'month';
    store.set('spending', S.spending);
    S.month = el.dataset.month; store.set('month', S.month);
    location.hash = '#/books/spending';
};
// One tap on fin's guess: the row takes the guessed type, as the resolve
// sheet would save it with the guess on screen.
ACT['home-guess-yes'] = async el => {
    const row = await findRow(Number(el.dataset.tx));
    if (!row) { toast('That row is no longer waiting', { bad: true }); return; }
    const pattern = suggestPattern(row.description);
    const body = { tx_id: row.id, service_name: el.dataset.merchant || row.service_name || titleCase(pattern || row.description),
        type_id: Number(el.dataset.type), apply_scope: pattern ? 'service_default' : 'transaction', pattern, match_type: 'contains', suggestion_visible: true };
    const r = await act('POST', '/api/transactions/resolve', body, 'Labelled');
    if (r) rerender();
};

ACT['looks-right'] = async el => {
    const r = await send('POST', '/api/changes/looked', { upto: Number(el.dataset.upto) });
    if (!r.ok) { toast(r.data.error || 'That did not work', { bad: true }); return; }
    toast('Marked as looked at. Nothing was undone.');
    rerender();
};
ACT['go-month-check'] = el => {
    S.month = el.dataset.month; store.set('month', S.month);
    if (location.hash !== '#/books') { afterNextRender = () => $('#month-check')?.scrollIntoView(); location.hash = '#/books'; }
    else { rerender(); setTimeout(() => $('#month-check')?.scrollIntoView(), 300); }
};
let afterNextRender = null;
ACT['needs-look-link'] = () => { S.needsLook = true; location.hash = '#/books'; };

// ---------------------------------------------------------------------------
// Queue
// ---------------------------------------------------------------------------

// The Queue's cards: rows with no type from one merchant share a card (Q1.3),
// each lane is split under what its rows hold out of the books (Q1.2), and
// fin's guess sits on the card with a one-tap "Yes" (Q2.1, Q2.2).
const queueOpen = {};
const QUEUE_PAGE = 6;
const queueGroups = new Map();     // a card's key -> its rows, for "This was… (all 4)"
const transferGuesses = new Map(); // a waiting transfer's id -> fin's guess for it
const LANE_GROUPS = {
    out: [['transfer', 'Held out of spending until you say what it was'], ['untyped', 'In spending as “No type”'],
        ['mixed', 'Mixed merchants: is the type right?'], ['bill', 'Bills with no payment seen']],
    in: [['transfer', 'Held out of income until you say what it was'], ['untyped', 'Refunds with no type'],
        ['mixed', 'Mixed merchants: is the type right?']],
};

/** Amounts added up, each currency apart: "S$ 12,200.00 · ₹ 4,000.00". */
function sumByCurrency(list, amountOf = i => i.amount) {
    const by = {};
    list.forEach(i => { by[i.currency] = (by[i.currency] || 0) + amountOf(i); });
    return Object.entries(by).map(([c, m]) => money(m, c)).join(' · ');
}
/** The untyped rows of a lane, one card per merchant text. */
function merchantCards(items) {
    const cards = [];
    const byText = new Map();
    items.forEach(i => {
        if (i.kind !== 'untyped') { cards.push({ ...i, rows: i.row ? [i.row] : [] }); return; }
        const text = (i.row.description || '').trim().toUpperCase();
        let card = byText.get(text);
        if (!card) { card = { ...i, key: `card-${i.row.id}`, rows: [], amount: 0 }; byText.set(text, card); cards.push(card); }
        card.rows.push(i.row);
        card.amount += i.amount;
    });
    return cards;
}

/** fin's stored type guess for a row with no type, as the card and the step
 *  show it. The server hands over the route; this only draws it. Returns
 *  {html, actions, top3, visible}. */
async function showResolveSuggestion(row, { n = 1, where = 'card' } = {}) {
    const none = { html: '', actions: '', top3: '', visible: false };
    let suggestion;
    try { suggestion = await get(`/api/transactions/${row.id}/suggestion`); } catch (_) { return none; }
    if (!suggestion || !suggestion.types || !suggestion.types.length) return none;
    const r = await refs();
    const share = p => Math.round(p * 100);
    const all = n > 1 ? ` (all ${n})` : '';
    const yes = (t, cls) => `<button type="button" class="btn ${cls}" data-act="guess-yes" data-tx="${row.id}" data-type="${t.type_id}" data-group="${esc(groupKeyOf(row))}">${icon('check-circle')}Yes, ${esc(t.name)}${all}</button>`;
    if (suggestion.route === 'prefill') {
        const top = suggestion.types[0];
        const book = r.typeById.get(top.type_id)?.proposed_book;
        return {
            html: `<div class="guess"><span>fin’s guess: <b>${esc(top.name)}</b>${book ? `, ${esc(book)}` : ''}</span>
                <span class="guess__meter" aria-hidden="true"><i style="width:${share(top.probability)}%"></i></span><span class="guess__sure">${share(top.probability)}% sure</span>
                ${where === 'step' ? `<div class="guess__act">${yes(top, 'primary')}</div>` : ''}</div>`,
            actions: yes(top, 'sm primary'), top3: '', visible: true,
        };
    }
    if (suggestion.route === 'top3') {
        const words = suggestion.types.map(t => `<b>${esc(t.name)}</b> ${share(t.probability)}%`).join(' · ');
        return {
            html: `<div class="guess"><span>fin’s guesses: ${words}</span>
                ${where === 'step' ? `<div class="guess__act">${suggestion.types.map(t => yes(t, 'subtle')).join('')}</div>` : ''}</div>`,
            actions: '', top3: words, visible: true,
        };
    }
    return none;
}

/** fin's guess for a waiting transfer, as words and the "Yes" it offers. */
function transferGuessWords(g) {
    const other = g.other_side ? esc(g.other_side) : 'that account';
    const words = {
        spending: [`<b>spending</b>, ${esc(g.type || '')}${g.book ? `, ${esc(g.book)}` : ''}`, `Yes, ${g.type || 'spending'}`],
        gift: ['<b>a gift</b>', 'Yes, a gift'],
        own_account: [`<b>a move to my own account</b>, ${other}`, `Yes, a move to ${g.other_side || 'it'}`],
        company: [`<b>money into a company</b>, ${other}`, `Yes, into ${g.other_side || 'the company'}`],
        loan_to_person: [`<b>a loan to a person</b>, ${other}`, `Yes, a loan to ${g.other_side || 'them'}`],
        loan_repayment: [`<b>a loan repayment</b>, ${other}`, `Yes, repaying ${g.other_side || 'the loan'}`],
        income: [`<b>income</b>, ${esc(g.type || '')}`, `Yes, income`],
    }[g.choice] || [esc(g.choice), 'Yes'];
    return { what: words[0], yes: words[1] };
}
function transferGuessHTML(row, g, { where = 'card' } = {}) {
    if (!g) return { html: '', actions: '' };
    const w = transferGuessWords(g);
    const p = Math.round(g.probability * 100);
    const yes = cls => `<button type="button" class="btn ${cls}" data-act="review-guess-yes" data-tx="${row.id}">${icon('check-circle')}${esc(w.yes)}</button>`;
    return {
        html: `<div class="guess"><span>fin’s guess: ${w.what}</span>
            <span class="guess__meter" aria-hidden="true"><i style="width:${p}%"></i></span><span class="guess__sure">${p}% sure · ${g.agree === g.of ? `the last ${g.of} went the same way` : `${g.agree} of the last ${g.of} went this way`}</span>
            ${where === 'step' ? `<div class="guess__act">${yes('primary')}</div>` : ''}</div>`,
        actions: yes('sm primary'),
    };
}
async function transferGuess(row) {
    try {
        const g = (await get(`/api/review/${row.id}/suggestion`)).guess;
        if (g) transferGuesses.set(row.id, g); else transferGuesses.delete(row.id);
        return g;
    } catch (_) { return null; }
}
function groupKeyOf(row) {
    for (const [key, rows] of queueGroups) if (rows.some(x => x.id === row.id)) return key;
    return '';
}

/** One card in a money lane: a transfer, a merchant's rows with no type, a
 *  mixed merchant's row or a missed bill. */
async function queueCardHTML(card) {
    const n = card.rows.length;
    if (n) queueGroups.set(card.key, card.rows);
    const row = card.rows[0];
    let guess = { html: '', actions: '' };
    if (card.kind === 'untyped') guess = await showResolveSuggestion(row, { n });
    else if (card.kind === 'transfer') guess = transferGuessHTML(row, await transferGuess(row));
    const amount = `${dirTag(card.lane)} <span class="num">${esc(sumByCurrency([card], c => c.amount))}</span>`;
    const marker = { untyped: tag('nofig', 'no type'), mixed: tag('notchecked', 'mixed merchant'), bill: tag('stale', 'missed bill') }[card.kind] || '';
    const count = n > 1 ? ` <span class="tag yours">${n} rows</span>` : '';
    let meta = card.meta;
    if (n > 1) {
        const dates = card.rows.map(x => x.date).sort();
        const accounts = new Set(card.rows.map(x => x.account_name));
        meta = `${dates[0] === dates[n - 1] ? day(dates[0]) : `${day(dates[0])} to ${day(dates[n - 1])}`} · ${accounts.size === 1 ? card.rows[0].account_name : plural(accounts.size, 'account')}`;
    }
    const data = a => Object.entries(a.data || {}).map(([k, v]) => ` data-${k}="${esc(v)}"`).join('') + (card.rows.length ? ` data-group="${esc(card.key)}"` : '');
    const all = n > 1 ? ` (all ${n})` : '';
    const own = (card.actions || []).map(a => a.href
        ? `<a class="btn sm${a.primary ? ' ink' : ''}" href="${a.href}">${esc(a.label)}</a>`
        : `<button type="button" class="btn sm${a.primary && !guess.actions ? ' ink' : ''}" data-act="${a.act}"${data(a)}>${esc(guess.actions && /^This was/.test(a.label) ? 'Something else' : a.label + (/^This was/.test(a.label) ? all : ''))}</button>`).join('');
    const seeRows = n > 1 ? `<details class="q-rows"><summary class="link">See the ${n} rows</summary>
        <table class="t table-tight"><tbody>${card.rows.map(x => `<tr><td>${esc(day(x.date))}</td><td>${esc(x.account_name || '')}</td>
            <td class="r num">${esc(money(Math.abs(toMinor(x.amount_sgd, x.currency)), x.currency))}</td></tr>`).join('')}</tbody></table></details>` : '';
    return `<article class="item q-card ${card.kind}">
        <div><div class="what">${esc(card.title)} ${marker}${count}${row ? rowMark(row.id) : ''}</div><div class="meta">${esc(meta || '')}</div>${card.holds && card.kind === 'mixed' ? `<div class="holds">${esc(card.holds)}</div>` : ''}</div>
        <div class="amt">${amount}</div>
        ${guess.html ? `<div class="q-guess">${guess.html}</div>` : ''}
        <div class="act">${guess.actions || ''}${own}</div>
        ${seeRows ? `<div class="q-more">${seeRows}</div>` : ''}</article>`;
}

async function laneHTML(key, title, list) {
    const groups = await Promise.all(LANE_GROUPS[key].map(async ([kind, heading]) => {
        const items = list.filter(i => i.kind === kind);
        if (!items.length) return '';
        const cards = merchantCards(items);
        const open = queueOpen[`${key}-${kind}`] || QUEUE_PAGE;
        const shown = await Promise.all(cards.slice(0, open).map(queueCardHTML));
        const more = cards.length - open;
        return `<div class="q-group">
            <h3 class="q-group__head"><span>${esc(heading)} <span class="q-group__n">· ${items.length}</span></span><span class="num">${esc(sumByCurrency(items))}</span></h3>
            ${shown.join('')}
            ${more > 0 ? `<button class="btn block q-show" data-act="queue-more" data-lane="${key}-${kind}">Show ${more} more</button>` : ''}</div>`;
    }));
    return `<section class="card card--lift lane-card" id="lane-${key}">
        <div class="section-header"><h2>${dirTag(key)} ${title}</h2><span class="section-header__aside"><b>${list.length}</b> ${list.length === 1 ? 'item' : 'items'}</span></div>
        ${list.length ? groups.join('') : '<p class="card-empty">Nothing waits here.</p>'}</section>`;
}

async function viewQueue() {
    const q = await loadQueue();
    queueGroups.clear();
    const [out, inn] = await Promise.all([laneHTML('out', 'Money out', q.lanes.out), laneHTML('in', 'Money in', q.lanes.in)]);
    const booksOpen = queueOpen.books || 12;
    const books = `<section class="card card--lift lane-card" id="lane-books">
        <div class="section-header"><div><h2>Figures and statements</h2><p>What the balances rest on</p></div><span class="section-header__aside"><b>${q.lanes.books.length}</b> ${q.lanes.books.length === 1 ? 'item' : 'items'}</span></div>
        ${q.lanes.books.length ? q.lanes.books.slice(0, booksOpen).map(itemHTML).join('') : '<p class="card-empty">Nothing waits here.</p>'}
        ${q.lanes.books.length > booksOpen ? `<button class="btn block q-show" data-act="queue-more" data-lane="books">Show all ${q.lanes.books.length}</button>` : ''}</section>`;
    const aside = q.lanes.aside.length ? `<section class="card lane-card" id="lane-aside"><div class="section-header"><div><h2>Set aside</h2>
        <p>Known, left as they are. Still marked refused on their accounts.</p></div><span class="section-header__aside"><b>${q.lanes.aside.length}</b> ${q.lanes.aside.length === 1 ? 'statement' : 'statements'}</span></div>${q.lanes.aside.map(itemHTML).join('')}</section>` : '';
    const chip = (to, cls, ic, label, n) => `<a class="delta-chip q-jump ${cls}" href="#/queue" data-act="jump" data-to="lane-${to}">${icon(ic)}${label} <span class="num">${n}</span></a>`;
    const chips = [
        q.lanes.out.length ? chip('out', 'k-out', 'arrow-circle-up', 'Money out', q.lanes.out.length) : '',
        q.lanes.in.length ? chip('in', 'k-in', 'arrow-circle-down', 'Money in', q.lanes.in.length) : '',
        q.lanes.books.length ? chip('books', 'delta-chip--warn', 'warning', 'Figures and statements', q.lanes.books.length) : '',
        q.lanes.aside.length ? chip('aside', 'q-jump--context', 'circle-dashed', 'Set aside', q.lanes.aside.length) : '',
    ].join('');
    const hero = `<section class="hero-card q-hero">
        <span class="eyebrow">Queue</span>
        ${q.count ? `<h1 class="q-hero__line"><span class="q-hero__count num">${q.count}</span> <span class="q-hero__words">${q.count === 1 ? 'thing waits' : 'things wait'} for you</span></h1>
            <p class="q-hero__sub">Money out and money in are kept apart, each under what it holds out of the books.</p>
            <div class="chip-row">${chips}</div>`
        : `<h1 class="q-hero__line"><span class="q-hero__words">Nothing waits for you</span></h1>
            <p class="q-empty">${icon('check-circle')}Every row has a label and every statement ties.</p>${chips ? `<div class="chip-row">${chips}</div>` : ''}`}
        ${q.capped.length ? `<p class="notice warn small" style="margin-top:12px">${icon('warning')}<span>Too many to read at once, so the counts and sums here are short: ${esc(q.capped.join('; '))}.</span></p>` : ''}</section>`;
    return `<div class="queue">${hero}
        <div class="waits-cols q-cols">${out}${inn}</div>
        ${books}${aside}</div>`;
}
ACT['queue-more'] = el => { queueOpen[el.dataset.lane] = 9999; rerender(); };
ACT.jump = el => $('#' + el.dataset.to)?.scrollIntoView({ behavior: 'smooth' });

async function findRow(txId) {
    const q = await loadQueue();
    for (const list of Object.values(q.lanes)) {
        const hit = list.find(i => i.row && i.row.id === txId);
        if (hit) return hit.row;
    }
    const one = await get(`/api/transactions?tx_id=${txId}`).catch(() => null);
    return one && one.transactions[0] || null;
}

function rowHead(row, { more = 0 } = {}) {
    const minor = toMinor(row.amount_sgd, row.currency);
    return `<div class="record-target"><div class="spread"><b>${esc(row.description)}</b>${rowAmount(minor, row.currency)}</div>
        <div class="small muted">${esc(day(row.date))} · ${esc(row.account_name || '')}${row.service_name ? ' · ' + esc(row.service_name) : ''}${more ? ` · and ${plural(more, 'more row')} from it` : ''}</div></div>`;
}

function typeOptions(types, selected, { kind = 'spending', blank = 'Choose a type' } = {}) {
    const list = types.filter(t => t.kind === kind);
    return `<option value="">${esc(blank)}</option>` + list.map(t =>
        `<option value="${t.id}"${String(t.id) === String(selected) ? ' selected' : ''}>${esc(t.display_name || t.name)}</option>`).join('');
}
function bookOptions(books, selected, blank = 'As the type says') {
    return `<option value="">${esc(blank)}</option>` + books.map(b => `<option value="${esc(b.name)}"${b.name === selected ? ' selected' : ''}>${esc(b.name)}</option>`).join('');
}

function suggestPattern(description) {
    let pattern = (description || '').toUpperCase();
    if (/\b(PAYNOW|FAST PAYMENT|TRANSFER|I-BANK|GIRO)\b/.test(pattern)) return '';
    pattern = pattern.replace(/\s+(SG|SGP|SIN|US|USA|GB|GBR|AU|AUS)\s*$/i, '').replace(/\s+\d{2}\/\d{2}$/, '');
    const parts = pattern.split(/\s+/);
    let cutoff = parts.length;
    for (let i = 1; i < parts.length; i++) {
        if (/^[A-Z0-9]{8,}$/.test(parts[i]) || /^\d+$/.test(parts[i])) { cutoff = i; break; }
    }
    return parts.slice(0, Math.max(cutoff, 2)).join(' ');
}
function titleCase(s) { return s.toLowerCase().replace(/\b\w/g, c => c.toUpperCase()); }

// "This was…": one step for every row that waits for a label (Q4.1). The row,
// then fin's guess with one "Yes" (Q4.2), then the choices; once one is picked
// the others fold away and its questions sit straight under it (Q4.4). For a
// row with no type, "spending" names the merchant, and which rows it labels is
// one plain line, with the rule's words behind "change" (Q4.3).
const CHOICE_ORDER = ['spending', 'own_account', 'company'];
const COUNT_WORDS = ['no', 'one', 'two', 'three', 'four', 'five', 'six', 'seven', 'eight'];
let step = null;
ACT['this-was'] = el => openStepFor(el);
ACT.resolve = el => openStepFor(el);
async function openStepFor(el) {
    const txId = Number(el.dataset.tx);
    const row = await findRow(txId);
    if (!row) { toast('That row is no longer waiting', { bad: true }); return; }
    const group = (el.dataset.group && queueGroups.get(el.dataset.group)) || [row];
    openWhatWas(row, group, { mixed: el.dataset.kind === 'mixed' });
}
async function openWhatWas(row, group, { mixed = false } = {}) {
    const [r, incomeKinds, services, q] = await Promise.all([refs(), get('/api/types?kind=income'), servicesList(), loadQueue()]);
    const minor = toMinor(row.amount_sgd, row.currency);
    const untyped = row.flow_type !== 'review';
    const pattern = suggestPattern(row.description);
    const merchant = row.service_name || titleCase(pattern || row.description || '');
    const waiting = q.lanes.out.concat(q.lanes.in).filter(i => i.kind === 'untyped' && i.row.id !== row.id);
    const others = pattern ? waiting.filter(i => (i.row.description || '').toUpperCase().includes(pattern)).length : group.length - 1;
    let guess;
    if (untyped) guess = await showResolveSuggestion(row, { n: group.length, where: 'step' });
    else guess = transferGuessHTML(row, await transferGuess(row), { where: 'step' });
    step = { row, group, untyped, mixed, choice: null, more: false, merchant, pattern, others, r, incomeKinds, services,
        suggestionVisible: !!(guess && guess.visible), guessed: !!(guess && guess.html) };
    const title = `${minor < 0 ? 'Money in' : 'Money out'} · what was it?`;
    openSheet(title, `${rowHead(row, { more: group.length - 1 })}
        <div id="ww-guess">${guess.html || ''}</div>
        <p class="ww-label" id="ww-label">${guess.html ? 'Or something else' : 'What was it?'}</p>
        <div class="choices" id="ww-choices"></div>
        <div id="ww-ask"></div>`, { eyebrow: group.length > 1 ? `This was… · all ${group.length} rows` : 'This was…' });
    drawChoices();
}
function drawChoices() {
    const { r, choice } = step;
    const all = r.review.choices;
    const tile = c => `<button type="button" class="choice" data-act="ww-pick" data-choice="${esc(c.name)}">
        <strong>${esc(c.name === 'spending' && step.guessed ? 'Spending, another type' : sentence(c.label))}</strong><span>${esc(c.description)}</span></button>`;
    const box = $('#ww-choices');
    if (!box) return;
    $('#ww-guess').hidden = !!choice;
    $('#ww-label').hidden = !!choice;
    if (choice) {
        box.innerHTML = `<div class="choice is-chosen" aria-live="polite"><div><strong>${esc(sentence(choice.label))}</strong><span>${esc(choice.description)}</span></div>
            <button type="button" class="link" data-act="ww-change">Change</button></div>`;
        return;
    }
    const first = CHOICE_ORDER.map(n => all.find(c => c.name === n)).filter(Boolean);
    const rest = all.filter(c => !CHOICE_ORDER.includes(c.name));
    box.innerHTML = first.map(tile).join('') + (step.more || !rest.length ? rest.map(tile).join('')
        : `<button type="button" class="choice more" data-act="ww-more"><strong>${esc(sentence(rest.map(c => c.label).slice(0, 3).join(', ')))}…</strong><span>${COUNT_WORDS[rest.length] || rest.length} more</span></button>`);
    $('#ww-ask').innerHTML = '';
}
ACT['ww-more'] = () => { step.more = true; drawChoices(); $('#ww-choices .choice:nth-child(4)')?.focus(); };
ACT['ww-change'] = () => { step.choice = null; drawChoices(); $('#ww-choices .choice')?.focus(); };
ACT['ww-pick'] = el => {
    const { r, row, untyped, incomeKinds } = step;
    const choice = r.review.choices.find(c => c.name === el.dataset.choice);
    step.choice = choice;
    drawChoices();
    let fields = '';
    if (choice.asks === 'type') {
        fields = `<label class="field"><span>Type</span><select id="cw-type">${typeOptions(r.types, row.type_id)}</select></label>
            <label class="field"><span>Book</span><select id="cw-book">${bookOptions(r.books, null, untyped ? 'As the merchant or type says' : 'As the type says')}</select></label>`;
    } else if (choice.asks === 'account') {
        const allowed = r.accounts.filter(a => choice.kinds.includes(a.type) && !step.group.some(x => x.account_id === a.id) && a.status !== 'archived');
        fields = `<label class="field"><span>Which account</span><select id="cw-account"><option value="">Choose</option>${allowed.map(a => `<option value="${a.id}">${esc(a.name)} (${esc(a.currency)})</option>`).join('')}</select></label>`;
    } else if (choice.asks === 'person') {
        const people = r.accounts.filter(a => a.type === 'person');
        fields = `<label class="field"><span>Who</span><select id="cw-person"><option value="">A new name…</option>${people.map(a => `<option value="${a.id}">${esc(a.name)}</option>`).join('')}</select></label>
            <label class="field"><span>New name</span><input type="text" id="cw-person-name" placeholder="Only for someone new"></label>`;
    } else if (choice.asks === 'income_kind') {
        fields = `<label class="field"><span>What kind of income</span><select id="cw-income">${typeOptions(incomeKinds, null, { kind: 'income', blank: 'Choose' })}</select></label>`;
    }
    const n = step.group.length;
    let scope = '';
    if (untyped && choice.name === 'spending') {
        const deflt = step.mixed ? 'transaction' : 'service_default';
        scope = `<div class="scope-line"><div><b id="ww-scope-words"></b><div class="small muted">${step.others ? `${plural(step.others, 'other row is', 'other rows are')} waiting from it` : 'No other row is waiting from it'}</div></div>
                <button type="button" class="link" data-act="ww-scope" aria-expanded="false" aria-controls="ww-scope-more">change</button></div>
            <div class="fields scope-more" id="ww-scope-more" hidden>
                <label class="field"><span>Merchant</span><input type="text" id="rs-merchant" list="rs-merchants" value="${esc(step.merchant)}" autocomplete="off"></label>
                <datalist id="rs-merchants">${step.services.map(s => `<option value="${esc(s.name)}">`).join('')}</datalist>
                <fieldset class="scope-pick"><legend class="sr-only">Which rows</legend>
                    <label class="check"><input type="radio" name="rs-scope" value="service_default"${deflt === 'service_default' ? ' checked' : ''}> Every row from this merchant</label>
                    <label class="check"><input type="radio" name="rs-scope" value="transaction"${deflt === 'transaction' ? ' checked' : ''}> Only this row</label>
                    <label class="check"><input type="radio" name="rs-scope" value="rule"> Rows whose text contains</label>
                    <input type="text" id="rs-pattern" value="${esc(step.pattern)}" aria-label="The text the rows contain"></fieldset></div>`;
    } else if (untyped) {
        scope = `<p class="small muted">${n > 1 ? `Labels these ${n} rows.` : 'Labels this row only.'}</p>`;
    }
    $('#ww-ask').innerHTML = `<div class="fields">${fields}</div>${scope}
        <button class="btn primary block" id="ww-save" data-act="ww-save">Save: ${esc(choice.label)}</button>`;
    const words = () => {
        const w = $('#ww-scope-words');
        if (!w) return;
        const s = $('input[name="rs-scope"]:checked')?.value;
        const name = $('#rs-merchant')?.value.trim() || step.merchant;
        w.textContent = s === 'transaction' ? 'Label only this row'
            : s === 'rule' ? `Label rows whose text contains ${$('#rs-pattern').value.trim() || '…'}` : `Label every row from ${name} the same way`;
    };
    const saveWords = () => {
        const t = $('#cw-type');
        const picked = t && t.value ? t.options[t.selectedIndex].text : '';
        $('#ww-save').textContent = `Save: ${choice.label}${picked ? ', ' + picked : ''}`;
    };
    $('#ww-ask').oninput = () => { words(); saveWords(); };
    $('#ww-ask').onchange = () => { words(); saveWords(); };
    words(); saveWords();
    setTimeout(() => ($('#ww-ask select, #ww-ask input') || $('#ww-save'))?.focus(), 30);
};
ACT['ww-scope'] = el => {
    const more = $('#ww-scope-more');
    more.hidden = !more.hidden;
    el.setAttribute('aria-expanded', String(!more.hidden));
    el.textContent = more.hidden ? 'change' : 'done';
};
/** What resolve takes for a row with no type and the type chosen for it. */
function resolveBody(row, typeId, { merchant, scope = 'service_default', pattern, book = '', visible = false } = {}) {
    const name = merchant || row.service_name || titleCase(suggestPattern(row.description) || row.description || '');
    const body = { tx_id: row.id, service_name: name, type_id: typeId, apply_scope: scope,
        pattern: pattern ?? suggestPattern(row.description), match_type: 'contains', suggestion_visible: visible };
    if (book) body.book = book;
    return body;
}
/** Save a type for a row with no type. A merchant text too plain to make a
 *  rule from (a PayNow or a GIRO) labels the card's other rows one by one. */
async function saveResolve(body, group = []) {
    const services = await servicesList();
    const svc = services.find(s => s.name.toLowerCase() === body.service_name.toLowerCase());
    if (svc) body.service_id = svc.id;
    const r = await act('POST', '/api/transactions/resolve', body, 'Saved');
    if (!r) return;
    // The group's other rows the rule did not reach (a merchant text too plain
    // for a rule, or rows whose text the pattern misses) are labelled one by one.
    const reached = new Set(r.data.backfilled_ids || []);
    const rest = body.apply_scope === 'transaction' || (body.apply_scope === 'rule' && body.pattern) ? []
        : group.filter(x => x.id !== body.tx_id && !reached.has(x.id) && !x.type_id);
    let done = 0;
    for (const x of rest) {
        const more = await send('POST', '/api/transactions/resolve', { ...body, tx_id: x.id, apply_scope: 'transaction', service_id: r.data.service_id ?? body.service_id });
        if (!more.ok) { toast(more.data.error || 'One of the rows could not be labelled', { bad: true }); break; }
        done += 1;
    }
    const others = done + (r.data.backfilled || 0);
    if (others) toast(`${plural(others, 'other row')} took the same label`);
    closeSheet(); rerender();
}
ACT['ww-save'] = async () => {
    const { row, choice, untyped, group } = step;
    const val = id => $('#' + id)?.value;
    if (untyped && choice.name === 'spending') {
        const typeId = Number(val('cw-type'));
        const name = (val('rs-merchant') || '').trim();
        const scope = $('input[name="rs-scope"]:checked').value;
        if (!name) { toast('Say which merchant it was', { bad: true }); return; }
        if (!typeId) { toast('Choose a type', { bad: true }); return; }
        const pattern = (val('rs-pattern') || '').trim();
        if (scope === 'rule' && !pattern) { toast('Say what text the rows contain', { bad: true }); return; }
        await saveResolve(resolveBody(row, typeId, { merchant: name, scope, pattern, book: val('cw-book'), visible: step.suggestionVisible }), group);
        return;
    }
    const body = { choice: choice.name };
    if (val('cw-type')) body.type_id = Number(val('cw-type'));
    if (val('cw-book')) body.book = val('cw-book');
    if (val('cw-account')) body.account_id = Number(val('cw-account'));
    if (val('cw-person')) body.account_id = Number(val('cw-person'));
    else if (val('cw-person-name')) body.person = val('cw-person-name').trim();
    if (val('cw-income')) body.income_kind_id = Number(val('cw-income'));
    const rows = untyped ? group : [row];
    if (rows.length === 1) {
        const r = await act('POST', `/api/review/${row.id}/label`, body, 'Labelled');
        if (r) { closeSheet(); rerender(); }
        return;
    }
    let done = 0;
    for (const x of rows) {
        const r = await send('POST', `/api/review/${x.id}/label`, body);
        if (!r.ok) { toast(`${done ? `${plural(done, 'row')} labelled; then: ` : ''}${r.data.error || 'that did not work'}`, { bad: true }); break; }
        done += 1;
    }
    if (done) { toast(`${plural(done, 'row')} labelled. Each can be undone in Changes.`); closeSheet(); rerender(); refreshFrame(); }
};
// "Yes, Groceries": fin's type guess, saved at once for the merchant's rows.
ACT['guess-yes'] = async el => {
    const row = await findRow(Number(el.dataset.tx));
    if (!row) { toast('That row is no longer waiting', { bad: true }); return; }
    el.disabled = true;
    await saveResolve(resolveBody(row, Number(el.dataset.type), { visible: true }), queueGroups.get(el.dataset.group) || [row]);
    el.disabled = false;
};
// "Yes, Rent": fin's guess for a transfer, from how the same payee was labelled before.
ACT['review-guess-yes'] = async el => {
    const g = transferGuesses.get(Number(el.dataset.tx));
    if (!g) return;
    el.disabled = true;
    const r = await act('POST', `/api/review/${el.dataset.tx}/label`, g.body, 'Labelled');
    el.disabled = false;
    if (r) { closeSheet(); rerender(); }
};

ACT['mixed-yes'] = async el => {
    const row = await findRow(Number(el.dataset.tx));
    if (!row) return;
    const r = await act('PUT', `/api/transactions/${row.id}`, { type_id: row.type_id, book: row.book }, `Kept ${row.display_type || 'its type'}`);
    if (r) rerender();
};

ACT.aside = async el => {
    const aside = el.dataset.aside === '1';
    const r = await send('POST', `/api/statements/refused/${el.dataset.id}/set-aside`, { aside });
    if (!r.ok) { toast(r.data.error || 'That did not work', { bad: true }); return; }
    toast(aside ? 'Set aside. It stays marked refused on its account.' : 'Back in the queue.');
    closeSheet(); rerender();
};

function refusedSum(f) {
    const cur = f.currency;
    return `<table class="sum"><tbody>
        <tr><td class="op"></td><td>Opening balance it states</td><td class="r">${esc(money(f.opening_minor, cur))}</td></tr>
        <tr><td class="op">${f.rows_minor < 0 ? '−' : '+'}</td><td>its ${plural(f.rows, 'row')}</td><td class="r">${esc(money(Math.abs(f.rows_minor), cur))}</td></tr>
        <tr class="eq"><td class="op">=</td><td>what the rows make it</td><td class="r">${esc(money(f.opening_minor + f.rows_minor, cur))}</td></tr>
        <tr><td class="op"></td><td>Closing balance it states</td><td class="r">${esc(money(f.closing_minor, cur))}</td></tr>
        <tr class="gap"><td class="op">≠</td><td>off by</td><td class="r">${esc(money(Math.abs(f.difference_minor), cur))}</td></tr></tbody></table>`;
}
ACT['refused-sum'] = async el => {
    const all = (await get('/api/statements/refused')).refused;
    const f = all.find(x => String(x.id) === el.dataset.id);
    if (!f) return;
    openSheet(esc(f.account_name), `<div class="ww-tags">${tag('refused', 'refused')} ${tag('off', `off by ${money(Math.abs(f.difference_minor), f.currency)}`)}${f.set_aside ? ' ' + tag('aside', 'set aside') : ''}</div>
        <p>The ${esc(day(f.statement_date))} statement does not tie, so none of its rows was written.
        The balance rests on the last statement that tied, plus the rows since.</p>
        <div class="record-target">${refusedSum(f)}</div>
        <p class="small muted">A row was probably missed when the file was read. Import a fixed file and it goes through the same tie check.</p>
        ${f.account_id ? `<a class="link small" href="#/books/account/${f.account_id}">The account</a>` : ''}`,
    { eyebrow: `Tie line · refused at upload ${esc(when(f.refused_at))}`,
        foot: `${f.set_aside ? `<button class="btn" data-act="aside" data-id="${f.id}" data-aside="0">Bring it back</button>`
            : `<button class="btn" data-act="aside" data-id="${f.id}" data-aside="1">Known, leave it</button>`}
            <a class="btn primary" href="#/books/import">Import a fixed file</a>` });
};

ACT['bill-pause'] = async el => {
    const r = await act('PUT', `/api/subscriptions/${el.dataset.sub}`, { status: 'paused' }, 'Bill marked paused');
    if (r) rerender();
};

// Enter a figure: one sheet, opened from the queue, an account's page and Lists.
// Opened from one account it names that account plainly and shows the figure
// it replaces (Q7.1, Q7.2); opened from Lists it offers the picker.
const KIND_PHRASE = { loan: 'a loan', holding: 'a holding', company: 'a company', person: 'a person', bank: 'a bank account', card: 'a card' };
const OWED_KINDS = ['loan'];   // account_kind.OWED_KINDS: typed positive, saved as owed
ACT.figure = async el => openFigure(el.dataset.account ? Number(el.dataset.account) : null);
async function openFigure(accountId) {
    const [r, sheet] = await Promise.all([refs(), sheetFor(currentMonth()).catch(() => null)]);
    const takes = r.accounts.filter(a => a.takes_a_figure && a.status !== 'archived');
    const named = takes.find(a => a.id === accountId);
    const chosen = named || takes[0];
    if (!chosen) { toast('No account takes a figure: add a loan, a holding, a company or a person in Lists', { bad: true }); return; }
    const lines = sheet ? sheet.sections.flatMap(s => s.lines).filter(l => !l.counted_in) : [];
    const sign = a => SIGNS[a.currency] || a.currency;
    const owes = a => OWED_KINDS.includes(a.type);
    const label = a => owes(a) ? `What is owed now, in ${sign(a)}` : a.type === 'holding' ? `What it is worth now, in ${sign(a)}` : `The balance now, in ${sign(a)}`;
    const target = a => {
        const l = lines.find(x => x.account_id === a.id);
        const ro = l && l.rests_on;
        const stale = ro && ro.source === 'supplied' && ro.age_days > STALE_DAYS;
        const now = l && l.balance_minor !== null && l.balance_minor !== undefined
            ? `<div class="fg-now">Now <b class="num${l.balance_minor < 0 ? ' neg' : ''}">${esc(money(l.balance_minor, l.currency))}</b> ${stale ? tag('stale', 'stale') : ''}</div>
                ${ro ? `<div class="small muted">${ro.source === 'supplied' ? 'your figure' : 'statement'} ${esc(day(ro.date))}, ${esc(plural(ro.age_days, 'day'))} old</div>` : ''}`
            : '<div class="small muted">No figure yet: it is left out of net worth until it has one.</div>';
        return `<div class="spread"><b>${esc(a.name)}</b>${named && takes.length > 1 ? '<button type="button" class="link" data-act="fg-another" aria-controls="fg-pick" aria-expanded="false">Another account</button>' : ''}</div>
            <div class="small">${esc(KIND_PHRASE[a.type] || a.type)}, in ${esc(sign(a))}${owes(a) ? ' · owed' : ''}</div>${now}`;
    };
    const hint = (a, typed) => {
        if (!owes(a)) return a.type === 'holding' ? 'What it is worth, as a plain number.' : 'The balance, as a plain number.';
        const n = Number(String(typed || '').replace(/[, ]/g, ''));
        const shown = typed && Number.isFinite(n) && n > 0 ? money(-toMinor(n, a.currency), a.currency) : `${sign(a)} −…`;
        return `Type the plain number. It is saved as owed and shows as ${shown}.`;
    };
    const placeholder = a => {
        const l = lines.find(x => x.account_id === a.id);
        return l && l.balance_minor ? groupFor(a.currency).format(Math.abs(l.balance_minor) / 10 ** digitsOf(a.currency)) : '0.00';
    };
    openSheet('Enter a figure', `<div class="record-target fg-target" id="fg-target">${target(chosen)}</div>
        <label class="field" id="fg-pick"${named ? ' hidden' : ''}><span>Account</span><select id="fg-account">${takes.map(a => `<option value="${a.id}"${a.id === chosen.id ? ' selected' : ''}>${esc(a.name)}, ${esc(KIND_PHRASE[a.type] || a.type)}, in ${esc(sign(a))}</option>`).join('')}</select></label>
        <div class="fields">
        <label class="field"><span id="fg-label">${esc(label(chosen))}</span><input type="text" inputmode="decimal" id="fg-amount" placeholder="${esc(placeholder(chosen))}" aria-describedby="fg-hint"></label>
        <p class="hint" id="fg-hint">${esc(hint(chosen))}</p>
        <label class="field"><span>On</span><input type="date" id="fg-date" value="${todayIso()}"></label>
        <label class="field"><span>Note</span><input type="text" id="fg-note" placeholder="Where the figure came from"></label></div>
        <p class="small muted">A figure you enter is the fact: nothing checks it. It shows as “your figure” with its date, and turns stale after ${STALE_DAYS} days.</p>`,
    { eyebrow: 'Your figure', foot: '<button class="btn primary" data-act="figure-save">Save the figure</button>' });
    const current = () => r.accountById.get(Number($('#fg-account').value));
    $('#fg-account').addEventListener('change', () => {
        const a = current();
        $('#fg-target').innerHTML = target(a);
        $('#fg-label').textContent = label(a);
        $('#fg-amount').placeholder = placeholder(a);
        $('#fg-hint').textContent = hint(a, $('#fg-amount').value);
    });
    $('#fg-amount').addEventListener('input', () => { $('#fg-hint').textContent = hint(current(), $('#fg-amount').value); });
}
ACT['fg-another'] = el => {
    const pick = $('#fg-pick');
    pick.hidden = false;
    el.setAttribute('aria-expanded', 'true');
    el.hidden = true;
    $('#fg-account').focus();
};
ACT['figure-save'] = async () => {
    const body = { account_id: Number($('#fg-account').value), amount: $('#fg-amount').value.replace(/[, ]/g, ''), date: $('#fg-date').value };
    const note = $('#fg-note').value.trim();
    if (note) body.note = note;
    const r = await act('POST', '/api/anchors', body, 'Figure entered');
    if (r) { if (r.data.message) toast(r.data.message); closeSheet(); rerender(); }
};

// ---------------------------------------------------------------------------
// Books: the balance sheet, as at a month's end, and its month check
// ---------------------------------------------------------------------------

/** Dates written in words, never as codes: "2026-08-31" becomes "31 Aug"
 *  (with the year when it is not the as-at month's). */
function dateWords(text) {
    return String(text ?? '').replace(/\b(\d{4})-(\d{2})-(\d{2})\b/g,
        iso => day(iso, { year: iso.slice(0, 4) !== S.month.slice(0, 4) }));
}
/** A saved rate in words: "1 INR = S$ 0.01528, rate saved 30 Sep". */
function rateWords(rate) {
    if (!rate) return '';
    const unit = (rate.pair || '').split('/')[0] || '';
    return `1 ${unit} = S$ ${rate.rate}, rate saved ${dateWords(rate.date)}`;
}
/** A company's or person's balance as a short sum, the zero parts dropped:
 *  "opening S$ 15,000.00 + paid for it S$ 1,830.99 (23 rows) = S$ 16,830.99". */
function madeOfSum(line) {
    const m = line.made_of;
    if (!m) return '';
    const person = line.kind === 'person';
    const parts = [];
    if (m.opening_minor) parts.push(['', 'opening', m.opening_minor]);
    if (m.paid_for_minor) parts.push(['+', person ? 'lent' : 'paid for it', m.paid_for_minor]);
    if (m.capital_minor) parts.push(['+', 'capital', m.capital_minor]);
    if (m.paid_back_minor) parts.push(['−', 'paid back', m.paid_back_minor]);
    if (!parts.length) return 'no money moved yet';
    if (parts.length === 1 && parts[0][0] === '') {
        return person ? `lent ${money(m.opening_minor)}, nothing paid back` : `opening ${money(m.opening_minor)}, nothing moved since`;
    }
    const onlyPaidFor = parts.filter(p => p[1] !== 'opening').length === 1 && m.paid_for_minor;
    const text = parts.map(([op, label, minor], i) => `${i || op ? op + ' ' : ''}${label} ${money(minor)}${onlyPaidFor && op === '+' && line.rows_since ? ` (${plural(line.rows_since, 'row')})` : ''}`).join(' ');
    return `${text} = ${money(line.balance_minor)}`;
}

const CHECK_ORDER = { off: 0, not_checked: 1, none: 2, ties: 3 };
function sortKey(line, key) {
    if (key === 'rests') return line.rests_on ? -line.rests_on.age_days : -99999;     // oldest first
    if (key === 'check') return CHECK_ORDER[line.check?.status || 'none'];
    if (key === 'value') return -(line.value_minor ?? -Infinity);
    return 0;
}

function marginNote(line, refused, asAt, { rate = true } = {}) {
    const notes = [];
    if (lacksRate(line)) notes.push(tag('nofig', `no ${line.currency} rate`, fixFor(line, asAt)));
    if (line.made_of) notes.push(esc(madeOfSum(line)));
    else {
        if (line.since) notes.push(`rolled forward: ${esc(line.since)}`);
        if (line.since_label && line.rows_since) notes.push(`${plural(line.rows_since, 'row')} ${esc(line.since_label)}`);
    }
    if (line.rate && rate) notes.push(esc(rateWords(line.rate)));
    if (line.left_out && line.balance !== 'no figure') notes.push(`left out: ${esc(dateWords(line.left_out))}`);
    if (line.note) notes.push(esc(line.note));
    if (line.archived) notes.push('archived');
    if (refused) notes.push(`${tag(refused.set_aside ? 'aside' : 'refused', `${day(refused.statement_date, { year: false })} statement refused`, { act: 'refused-sum', data: { id: refused.id } })}`);
    return notes.join(' · ');
}
/** The figure a line holds, or the one marker saying why it has none. */
function lineFigure(line, asAt) {
    if (line.in_total) return `<span class="num">${esc(money(line.value_minor, 'SGD'))}</span>`;
    if (line.counted_in) return '<span class="muted">counted above</span>';
    return tag('nofig', line.balance === 'no figure' ? 'no figure' : lacksRate(line) ? 'no rate' : 'left out', fixFor(line, asAt));
}
/** The key to the tick column, said once under the sheet. */
function tickKey() {
    const k = (cls, ic, words) => `<span><span class="tick ${cls}" aria-hidden="true">${ic}</span>${words}</span>`;
    return `<p class="tick-key"><b>Tick marks</b>${k('ties', icon('check-circle'), 'ties to its statement')}${k('off', icon('prohibit'), 'off by: the rows do not add up to the statement')}${k('notchecked', icon('circle-dashed'), 'not checked: nothing to check against')}${k('yours', icon('user-circle'), 'your figure: no check, its age is printed')}${k('rows', '=', 'worked out from rows')}</p>`;
}

async function viewSheet() {
    const [sheet, q] = await Promise.all([sheetFor(S.month), loadQueue(), refs()]);
    const refusedBy = q.refusedBy;
    const allLines = sheet.sections.flatMap(s => s.lines);
    const needCount = allLines.filter(l => lineNeedsLook(l, refusedBy)).length;
    const sorted = lines => {
        if (!S.sheetSort.key) return lines;
        const dir = S.sheetSort.dir === 'asc' ? 1 : -1;
        return [...lines].sort((a, b) => (sortKey(a, S.sheetSort.key) - sortKey(b, S.sheetSort.key)) * dir);
    };
    const shown = line => !S.needsLook || lineNeedsLook(line, refusedBy);
    const sortBtn = (key, label) => `<button data-act="sheet-sort" data-key="${key}"${S.sheetSort.key === key ? ` data-dir="${S.sheetSort.dir}"` : ''}>${label}</button>`;
    const owedNote = section => section.owed ? ' <span class="sec-note">negative: what we owe</span>' : '';

    const table = sheet.sections.map(section => {
        const lines = sorted(section.lines.filter(shown));
        if (!lines.length && S.needsLook) return '';
        const rows = lines.map(line => `<tr>
            <td class="acct-name"><a href="#/books/account/${line.account_id}">${esc(line.name)}</a>${accountMark(line.account_id)}</td>
            <td class="r num">${line.currency !== 'SGD' && line.balance_minor !== null ? esc(money(line.balance_minor, line.currency)) : ''}</td>
            <td class="r">${lineFigure(line, sheet.as_at)}</td>
            <td class="tick-cell">${tickOf(line)}</td>
            <td class="rests">${restsOn(line)}</td>
            <td>${checkMarker(line)}</td>
            <td class="margin-note">${marginNote(line, refusedBy.get(line.account_id), sheet.as_at)}</td></tr>`).join('');
        return `<tr class="section"><td colspan="7">${esc(section.heading)}${owedNote(section)}</td></tr>${rows}
            ${S.needsLook ? '' : `<tr class="total"><td>${section.owed ? 'Total owed' : 'Total'}</td><td></td><td class="r num">${esc(money(section.total_minor, 'SGD'))}</td><td colspan="4" class="sec-left">${section.left_out.length ? `left out: ${section.left_out.map(l => esc(l.name)).join(', ')}` : ''}</td></tr>`}`;
    }).join('');

    // The phone: one line card per account; "no figure" said once, where the amount would be.
    const cards = sheet.sections.map(section => {
        const lines = sorted(section.lines.filter(shown));
        if (!lines.length && S.needsLook) return '';
        return `<div class="line-group"><h3 class="line-group__head">${esc(section.heading)}${owedNote(section)}</h3>${lines.map(line => {
            const foreign = line.currency !== 'SGD' && line.balance_minor !== null;
            const note = marginNote(line, refusedBy.get(line.account_id), sheet.as_at, { rate: !foreign });
            return `<div class="line-card">
            <div class="line-card__head"><span class="line-card__name"><a href="#/books/account/${line.account_id}">${esc(line.name)}</a>${accountMark(line.account_id)}</span>
                <span class="line-card__fig">${line.counted_in ? '' : lineFigure(line, sheet.as_at)}</span></div>
            ${foreign ? `<div class="line-card__sub"><span class="num">${esc(money(line.balance_minor, line.currency))}</span> <span class="muted">at ${esc(rateWords(line.rate))}</span></div>` : ''}
            <div class="line-card__sub">${tickOf(line)}<span>${restsOn(line, { short: true })}</span></div>
            <div class="line-card__tags">${checkMarker(line)}</div>
            ${note ? `<div class="margin-note">${note}</div>` : ''}</div>`;
        }).join('')}
            ${S.needsLook ? '' : `<div class="line-total"><b>${section.owed ? 'Total owed' : 'Total'}</b><b class="num">${esc(money(section.total_minor, 'SGD'))}</b></div>`}</div>`;
    }).join('');

    const cc = sheet.currency_change;
    afterRender(() => drawBridge(sheet));
    return `${booksNav('sheet')}
    <div class="page-head"><div><span class="eyebrow">Books · Balance sheet</span><h1>As at ${esc(day(sheet.as_at))}${S.month === currentMonth() ? ' <span class="muted">this month so far</span>' : ''}</h1>
        <p>Every balance names what it rests on. What is owed is negative, under a heading that says owed. Amounts in S$; other currencies at the saved rate.</p></div>
        ${monthPicker()}</div>
    <section class="hero-card sheet-hero">
        <div class="hero-card__head"><div><span class="eyebrow">Net worth</span><div class="hero-figure">${heroFigure(sheet.net_worth_minor, 'SGD')}</div></div>
            <div class="row"><button class="btn" data-act="figure">${icon('pencil-simple')}Enter a figure</button><a class="btn" href="#/queue">${icon('list-bullets')}Queue <span class="count">${q.count || ''}</span></a></div></div>
        ${sheet.note ? `<div class="chip-row left-out-row"><span class="delta-chip delta-chip--warn">${icon('warning')}${plural(sheet.left_out.length, 'line')} left out</span><span class="small">${esc(dateWords(sheet.note).replace(/^Left out of the total: /, ''))}</span></div>` : ''}
        ${restsOnHTML(sheet)}
    </section>
    <section class="card sheet-card">
        <div class="section-header"><h2>What we have and what we owe</h2>
            <div class="row"><div class="seg" role="group" aria-label="Which lines">
                <button data-act="needs-look" data-on="0" aria-pressed="${!S.needsLook}">All lines</button>
                <button data-act="needs-look" data-on="1" aria-pressed="${S.needsLook}">${icon('warning')}Needs a look · ${needCount}</button></div>
                <label class="field phone-only sheet-sort"><select data-change="sheet-sort-select" aria-label="Sort">
                    <option value="">Sheet order</option><option value="rests"${S.sheetSort.key === 'rests' ? ' selected' : ''}>Rests on: oldest first</option>
                    <option value="check"${S.sheetSort.key === 'check' ? ' selected' : ''}>Check: worst first</option></select></label></div></div>
        <div class="table-shell sheet-table"><table class="t">
            <thead><tr><th style="min-width:170px">Account</th><th class="r">In its currency</th><th class="r">${sortBtn('value', 'In S$')}</th><th class="tick-cell"><span class="sr-only">Tick</span></th><th>${sortBtn('rests', 'Rests on')}</th><th>${sortBtn('check', 'Check')}</th><th>Margin note</th></tr></thead>
            <tbody>${table || '<tr><td colspan="7" class="empty">Nothing needs a look.</td></tr>'}</tbody>
            ${S.needsLook ? '' : `<tfoot><tr class="total"><td>Net worth</td><td></td><td class="r num">${esc(money(sheet.net_worth_minor, 'SGD'))}</td><td colspan="4"></td></tr></tfoot>`}</table></div>
        <div class="sheet-lines">${cards || '<p class="card-empty">Nothing needs a look.</p>'}
            ${S.needsLook ? '' : `<div class="line-total line-total--worth"><b>Net worth</b><b class="num">${esc(money(sheet.net_worth_minor, 'SGD'))}</b></div>`}</div>
        ${tickKey()}
        ${cc && cc.minor ? `<p class="card-foot">Currency change since ${esc(day(cc.from))}: <b class="num">${esc(money(cc.minor, 'SGD', { signed: true }))}</b></p>` : ''}
        ${cc && cc.minor === null && cc.note ? `<p class="card-foot">${tag('nofig', 'currency change not worked out')} ${esc(dateWords(cc.note))}
            ${[...new Set((cc.lines || []).map(l => l.currency).filter(Boolean))].map(c => `<button class="btn sm" data-act="rate" data-currency="${esc(c)}" data-date="${esc(rateDay(sheet.as_at))}">${esc(c)} rate</button>`).join(' ')}</p>` : ''}
    </section>
    ${monthCheckHTML(sheet)}`;
}
ACT['needs-look'] = el => { S.needsLook = el.dataset.on === '1'; rerender(); };
ACT['sheet-sort'] = el => {
    const key = el.dataset.key;
    if (S.sheetSort.key === key) {
        if (S.sheetSort.dir === 'asc') S.sheetSort.dir = 'desc'; else S.sheetSort = { key: null, dir: 'asc' };
    } else S.sheetSort = { key, dir: 'asc' };
    rerender();
};
ACT['sheet-sort-select'] = el => { S.sheetSort = { key: el.value || null, dir: 'asc' }; rerender(); };

/** Nothing moved in the month: no income, spending, currency change or money
 *  moved to accounts left out. */
function monthStill(mc) {
    return !mc.income_minor && !mc.spending_minor && !mc.currency_change_minor && !mc.outside_minor;
}
/** Why the month check does not add up, or what is still worth a look: one
 *  row per account, its marker under its name, How much holding money only. */
function monthReasons(mc) {
    const rows = [];
    const rv = mc.review || {};
    const label = { href: '#/queue', text: 'Label them' };
    if (rv.out_count) rows.push({ what: `${dirTag('out')} ${plural(rv.out_count, 'transfer')} waiting for a label`, how: money(rv.out_minor, 'SGD'), fix: label });
    if (rv.in_count) rows.push({ what: `${dirTag('in')} ${plural(rv.in_count, 'transfer')} waiting for a label`, how: money(rv.in_minor, 'SGD'), fix: label });
    const byName = new Map();
    (mc.not_tying || []).forEach(l => {
        const marker = l.status === 'off'
            ? tag('off', `off by ${money(Math.abs(l.difference_minor), l.currency)}`)
            : tag('notchecked', dateWords(l.text).replace(/^not checked \((.*)\)$/, 'not checked: $1').replace(/ to check against$/, ''));
        byName.set(l.name, { what: `<b>${esc(l.name)}</b>`, marker, how: null, fix: { href: `#/books/account/${l.account_id}`, text: 'See the tie line' } });
    });
    (mc.left_out || []).forEach(l => {
        const acct = l.account_id ? window.__accountById?.get(l.account_id) : null;
        const why = l.why === 'no figure' ? 'no figure' : /^no balance on /.test(l.why) ? `no figure on ${dateWords(l.why.replace(/^no balance on /, ''))}` : dateWords(l.why);
        const fix = acct && acct.takes_a_figure ? { act: 'figure', account: acct.id, text: 'Enter a figure' }
            : acct ? { href: '#/books/import', text: 'Import a statement' } : { act: 'figure', text: 'Enter a figure' };
        byName.set(l.name, { what: `<b>${esc(l.name)}</b>`, marker: tag('nofig', `${why}, left out of both sides`), how: null, fix });
    });
    rows.push(...byName.values());
    const fixHTML = f => f.href ? `<a class="link" href="${f.href}">${f.text}</a>`
        : `<button class="link" data-act="${f.act}"${f.account ? ` data-account="${f.account}"` : ''}>${f.text}</button>`;
    return rows.map(r => `<tr><td><div class="reason-what">${r.what}</div>${r.marker ? `<div class="reason-mark">${r.marker}</div>` : ''}</td>
        <td class="r num reason-how">${r.how ? esc(r.how) : '<span class="muted" aria-label="no sum of money">—</span>'}</td><td class="reason-fix">${fixHTML(r.fix)}</td></tr>`).join('');
}

function monthCheckHTML(sheet) {
    const mc = sheet.month_check;
    if (!mc) return '';
    const head = `<div class="section-header"><div><p class="eyebrow">Month check · ${esc(monthName(sheet.month))}</p>
        <h2>Do the month’s rows account for the change in net worth?</h2></div></div>`;
    if (!mc.available || mc.unexplained_minor === null || mc.unexplained_minor === undefined) {
        return `<section class="card month-check" id="month-check">${head}<p class="card-empty">${esc(dateWords(mc.why) || 'Not worked out for this month.')}</p></section>`;
    }
    const line = (op, label, minor, cls = '') => `<tr class="${cls}"><td class="op">${op}</td><td>${label}</td><td class="r">${esc(money(minor, 'SGD'))}</td></tr>`;
    const signOp = m => (m < 0 ? '−' : '+');
    const unexplained = mc.unexplained_minor;
    const leftOut = (mc.left_out || []).length;
    const inCheck = mc.in_check_count;
    const sum = `<table class="sum"><tbody>
        ${line('', `Net worth at ${esc(dateWords(mc.from))} <span class="muted small">(${inCheck != null ? plural(inCheck, 'account') : 'the accounts'} in the check)</span>`, mc.opening_minor)}
        ${line('+', 'income', mc.income_minor)}
        ${line('−', `household spending${mc.interest_minor ? `, with ${esc(money(mc.interest_minor, 'SGD'))} loan interest` : ''}`, mc.spending_minor)}
        ${line(signOp(mc.currency_change_minor), 'currency change', Math.abs(mc.currency_change_minor))}
        ${mc.outside_minor ? line(signOp(mc.outside_minor), 'money moved to or from accounts left out', Math.abs(mc.outside_minor)) : ''}
        ${line('=', `what it should be at ${esc(dateWords(mc.to))}`, mc.expected_minor, 'eq')}
        ${line('', 'what it is', mc.actual_minor)}
        </tbody></table>`;
    // A green tick only when nothing is left out; otherwise say for how many it adds up.
    const result = unexplained !== 0
        ? `<span class="delta-chip delta-chip--down mc-chip">${icon('warning-circle')}${esc(money(unexplained, 'SGD'))} unexplained</span>`
        : leftOut
            ? `<div class="mc-partial"><span class="mc-partial__op" aria-hidden="true">=</span><div><b>Adds up for the ${inCheck != null ? plural(inCheck, 'account') : 'accounts'} in the check.</b>
                <span>${plural(leftOut, 'account is', 'accounts are')} left out, so part of ${esc(monthName(sheet.month).split(' ')[0])} is not checked. ${leftOut === 1 ? 'It is' : 'They are'} listed below.</span></div></div>`
            : `<span class="delta-chip delta-chip--up mc-chip">${icon('check-circle')}it adds up · ${esc(money(0, 'SGD'))}</span>`;
    const still = monthStill(mc);
    const right = still
        ? `<div class="bridge-still"><p class="card-empty">${S.month === currentMonth() || sheet.month === currentMonth() ? 'Nothing has moved yet this month.' : `Nothing moved in ${esc(monthName(sheet.month))}.`}</p></div>`
        : `<div class="desk-only bridge-box"><p class="eyebrow">The bridge</p><svg class="bridge" id="bridge" viewBox="0 0 600 270" role="img" aria-label="The month check as a bridge"></svg><p class="card-foot bridge-note" id="bridge-note"></p></div>`;
    const reasons = monthReasons(mc);
    const whyTable = `<div class="mc-reasons"><h3>${unexplained === 0 ? 'Still worth a look' : 'Why it does not add up'}</h3>
        ${reasons ? `<div class="table-shell"><table class="t reasons"><thead><tr><th>Where to look</th><th class="r">How much</th><th>Fix</th></tr></thead><tbody>${reasons}</tbody></table></div>`
            : `<p class="card-empty">${unexplained === 0 ? 'Nothing: every row is labelled and every line ties.' : 'Nothing waits and every line ties: a row may be missing or mislabelled, or a figure moved.'}</p>`}</div>`;
    return `<section class="card month-check" id="month-check">${head}
        <div class="mc-grid"><div>${sum}<div class="mc-result">${result}</div></div>${right}</div>
        ${whyTable}</section>`;
}

/** The month check as a bridge (desk only): the start and the end totals as
 *  bars on a labelled axis that says where it starts, each step floating
 *  from where the last ended, and the unexplained gap as its own hatched step. */
function drawBridge(sheet) {
    const svg = $('#bridge');
    const mc = sheet.month_check;
    if (!svg || !mc || !mc.available || mc.unexplained_minor === null) return;
    const steps = [
        [day(mc.from, { year: false }), mc.opening_minor, 'base'], ['Income', mc.income_minor, 'in'], ['Spending', -mc.spending_minor, 'out'],
        ['Currency', mc.currency_change_minor, 'move'],
    ];
    if (mc.outside_minor) steps.push(['Outside', mc.outside_minor, 'move']);
    steps.push(['Should be', mc.expected_minor, 'base']);
    if (mc.unexplained_minor) steps.push(['Unexplained', mc.unexplained_minor, 'gap']);
    steps.push(['Is', mc.actual_minor, 'actual']);
    let running = 0;
    const bars = steps.map(([label, v, kind], i) => {
        if (kind === 'base' || kind === 'actual') { running = v; return { label, from: null, to: v, v, kind }; }
        const from = running; running += v;
        return { label, from, to: running, v, kind };
    });
    const ends = bars.flatMap(b => b.from === null ? [b.to] : [b.from, b.to]);
    const max = Math.max(...ends), min = Math.min(...ends);
    const span = Math.max(max - min, Math.abs(max) * 0.01, 100);
    const raw = span / 4;
    const mag = 10 ** Math.floor(Math.log10(raw));
    const step = [1, 2, 2.5, 5, 10].map(m => m * mag).find(s => s >= raw);
    let lo = Math.floor((min - span * 0.15) / step) * step;
    if (min >= 0 && lo < 0) lo = 0;
    const hi = Math.ceil((max + span * 0.05) / step) * step;
    const W = 600, H = 270, left = 58, right = 8, top = 22, bottom = 222;
    const n = bars.length, bw = (W - left - right) / n;
    const y = v => bottom - ((v - lo) / (hi - lo)) * (bottom - top);
    const axisWord = v => {
        const s = v / 100;
        return Math.abs(s) >= 1e6 ? `${+(s / 1e6).toFixed(2)}M` : Math.abs(s) >= 1e3 ? `${+(s / 1e3).toFixed(1)}k` : `${s}`;
    };
    let out = `<defs><pattern id="bridge-hatch" width="6" height="6" patternUnits="userSpaceOnUse" patternTransform="rotate(45)"><rect width="6" height="6" class="b-hatch-bg"/><line x1="0" y1="0" x2="0" y2="6" class="b-hatch-line"/></pattern></defs>`;
    for (let v = lo; v <= hi + 1; v += step) {
        out += `<line class="b-grid" x1="${left}" x2="${W - right}" y1="${y(v)}" y2="${y(v)}"/><text class="b-axis" x="${left - 8}" y="${y(v) + 3.5}" text-anchor="end">${esc(axisWord(v))}</text>`;
    }
    out += `<line class="b-base" x1="${left}" x2="${W - right}" y1="${bottom}" y2="${bottom}"/>`;
    bars.forEach((b, i) => {
        const x = left + i * bw + bw * 0.16, w = bw * 0.68;
        const from = b.from === null ? lo : b.from;
        const y1 = y(Math.max(from, b.to)), y2 = y(Math.min(from, b.to));
        out += `<rect class="b-${b.kind}" x="${x.toFixed(1)}" y="${y1.toFixed(1)}" width="${w.toFixed(1)}" height="${Math.max(2, y2 - y1).toFixed(1)}" rx="3"/>`;
        out += `<text class="b-label" x="${x + w / 2}" y="${bottom + 16}" text-anchor="middle">${esc(b.label)}</text>`;
        const shown = b.from !== null ? money(b.v, 'SGD', { signed: true }) : money(b.v, 'SGD');
        out += `<text class="b-fig${b.kind === 'gap' ? ' b-fig--gap' : ''}" x="${x + w / 2}" y="${y1 - 6}" text-anchor="middle">${esc(shown.replace('S$ ', ''))}</text>`;
    });
    svg.innerHTML = out;
    const note = $('#bridge-note');
    if (note) note.textContent = lo === 0 ? 'The axis starts at zero; each step is drawn to scale. Figures in S$.' : `The axis starts at ${money(lo, 'SGD')}, not at zero; each step is drawn to scale. Figures in S$.`;
}

// ---------------------------------------------------------------------------
// One account's page: its balance, its tie line, its figures and rows
// ---------------------------------------------------------------------------

const KIND_WORDS = { bank: 'Bank account', card: 'Card', loan: 'Loan', holding: 'Holding', company: 'Company', person: 'Person' };
/** What a row is labelled, as an account's rows show it. */
function acctRowLabel(row) {
    const flowWord = { review: tag('nofig', 'waiting'), transfer: '<span class="muted">transfer</span>', payment: '<span class="muted">card payment</span>', movement: `<span class="muted">movement${row.other_side_name ? ' · ' + esc(row.other_side_name) : ''}</span>`, income: '<span class="muted">income</span>' }[row.flow_type];
    return flowWord || (row.display_type ? esc(row.display_type) : tag('nofig', 'no type'));
}
/** One of an account's rows: money in and money out in two plain columns. */
function acctRowTr(row) {
    const minor = toMinor(row.amount_sgd, row.currency);
    const cur = row.currency || 'SGD';
    const inCell = minor !== null && minor < 0 ? esc(money(-minor, cur)) : '';
    const outCell = minor !== null && minor >= 0 ? esc(money(minor, cur)) : '';
    return `<tr class="clickable" data-act="row" data-tx="${row.id}">
        <td class="num row-date">${esc(day(row.date, { year: false }))}</td>
        <td class="row-desc">${esc(row.description)}${rowMark(row.id)}${row.notes ? ` <span class="muted small">· ${esc(row.notes)}</span>` : ''}${row.is_one_off ? ' <span class="tag yours">one-off</span>' : ''}${row.book && row.book !== 'Household' ? ` <span class="small muted">· ${esc(row.book)}</span>` : ''}</td>
        <td class="row-label">${acctRowLabel(row)}</td>
        <td class="r num row-in">${inCell}</td><td class="r num row-out">${outCell}</td></tr>`;
}
/** A tie line's check as its one pill. */
function tieTag(t, cur) {
    if (t.status === 'ties') return tag('ties', 'ties');
    if (t.status === 'off') return tag('off', `off by ${money(Math.abs(t.difference_minor), cur)}`);
    if (t.status === 'your figure') return tag('yours', 'your figure');
    return tag('notchecked', 'not checked: no earlier balance');
}
/** Between two of your figures: the earlier figure, the rows in between (in
 *  and out apart), against the later figure, and what moved with no row. */
function betweenFigures(t, cur) {
    const made = t.opening_minor + (t.in_minor || 0) - (t.out_minor || 0);
    return `<table class="sum"><tbody>
        <tr><td class="op"></td><td>your figure ${esc(day(t.opening_date))}</td><td class="r">${esc(money(t.opening_minor, cur))}</td></tr>
        <tr><td class="op">+</td><td>money in${t.rows ? '' : ' <span class="muted small">(no rows in between)</span>'}</td><td class="r">${esc(money(t.in_minor || 0, cur))}</td></tr>
        <tr><td class="op">−</td><td>money out${t.rows ? ` <span class="muted small">(${plural(t.rows, 'row')} in between)</span>` : ''}</td><td class="r">${esc(money(t.out_minor || 0, cur))}</td></tr>
        <tr class="eq"><td class="op">=</td><td>what the rows make it</td><td class="r">${esc(money(made, cur))}</td></tr>
        <tr><td class="op"></td><td>your figure ${esc(day(t.date))}</td><td class="r">${esc(money(t.closing_minor, cur))}</td></tr>
        <tr class="moved"><td class="op">?</td><td><b>moved with no row</b></td><td class="r">${esc(money(t.moved_minor ?? (t.closing_minor - made), cur, { signed: true }))}</td></tr>
        </tbody></table>`;
}

const accountPage = { page: 1 };
async function viewAccount(id) {
    const [r, sheet, ties, anchorsList, refused] = await Promise.all([
        refs(), sheetFor(S.month), get(`/api/accounts/${id}/ties`), get(`/api/anchors?account_id=${id}`), get(`/api/statements/refused?account_id=${id}`),
    ]);
    const acct = r.accountById.get(id);
    if (!acct) throw new Error('No such account');
    if (accountPage.id !== id) { accountPage.id = id; accountPage.page = 1; }
    const rows = await get(`/api/transactions?account_id=${id}&per_page=50&page=${accountPage.page}`);
    const line = sheet.sections.flatMap(s => s.lines).find(l => l.account_id === id);
    const cur = acct.currency || 'SGD';
    const asAtShort = day(sheet.as_at, { year: false });

    // The balance as a sum, in tiles: what it rests on, the rows since, the balance.
    let tiles = '';
    if (line && line.rests_on && line.balance_minor !== null) {
        const anchor = anchorsList.find(a => a.date === line.rests_on.date);
        const base = anchor ? anchor.amount_minor : null;
        const change = base !== null ? line.balance_minor - base : null;
        tiles = `<div class="stat-tiles acct-tiles">
            <div class="stat-tile"><span class="stat-tile__label">${line.rests_on.source === 'supplied' ? 'Your figure' : 'Statement'} ${esc(day(line.rests_on.date, { year: false }))}</span><span class="stat-tile__figure">${esc(money(base, cur))}</span></div>
            ${change !== null ? `<div class="stat-tile"><span class="stat-tile__label">${change < 0 ? '−' : '+'} rows since</span><span class="stat-tile__figure">${esc(money(Math.abs(change), cur))}</span><span class="stat-tile__sub">${esc(line.since || 'nothing since')}</span></div>` : ''}
            <div class="stat-tile"><span class="stat-tile__label">= Balance ${esc(asAtShort)}</span><span class="stat-tile__figure">${esc(money(line.balance_minor, cur))}</span></div>
            ${cur !== 'SGD' && line.value_minor !== null ? `<div class="stat-tile"><span class="stat-tile__label">In S$</span><span class="stat-tile__figure">${esc(money(line.value_minor, 'SGD'))}</span><span class="stat-tile__sub">${esc(rateWords(line.rate))}</span></div>` : ''}
            </div>`;
    } else if (line && line.made_of) {
        tiles = `<p class="acct-made">${esc(madeOfSum(line))}</p>`;
    }
    // One marker, never the symbol and the pill both.
    const marker = !line ? '' : line.check ? checkMarker(line) : line.rests_on?.source === 'supplied' ? tag('yours', 'your figure') : `<span class="muted small">${line.made_of ? 'worked out from rows' : 'no check possible'}</span>`;
    const hero = `<section class="hero-card acct-hero">
        <div class="hero-card__head"><div><span class="eyebrow">Balance at ${esc(day(sheet.as_at))}</span>
            <div class="hero-figure">${line && line.balance_minor !== null ? heroFigure(line.balance_minor, cur) : ''}</div></div>
            <div class="acct-hero__mark">${line && line.balance_minor === null ? lineFigure(line, sheet.as_at) : marker}</div></div>
        <p class="small acct-rests">${line && line.balance_minor !== null ? `Rests on: ${restsOn(line)}` : 'Nothing to rest on yet.'}</p>
        ${tiles}${line && line.made_of && tiles.indexOf('acct-made') < 0 ? `<p class="acct-made">${esc(madeOfSum(line))}</p>` : ''}</section>`;

    // Tie lines: each statement a row, money in and money out apart; on an
    // account that rests on your figures, what moved between them instead.
    const onFigures = ties.ties.length > 0 && ties.ties.every(t => t.status === 'your figure');
    let tieCard;
    if (onFigures) {
        const pairs = ties.ties.filter(t => t.opening_minor !== null && t.opening_minor !== undefined);
        // One figure alone has nothing to set it against: no card.
        tieCard = pairs.length ? `<section class="card"><div class="section-header"><h2>Between your figures</h2>${tag('yours', 'your figure')}</div>
            ${pairs.map(t => betweenFigures(t, cur)).join('<hr class="rule">')}
            <p class="card-foot">Nothing checks a figure you enter; this only shows what moved between them.</p></section>` : '';
    } else {
        const dash = '<span class="muted">—</span>';
        const tieRows = ties.ties.map(t => {
            const worked = t.opening_minor !== null && t.opening_minor !== undefined;
            const made = worked ? t.opening_minor + (t.in_minor || 0) - (t.out_minor || 0) : null;
            const title = t.status === 'your figure' ? 'Your figure' : 'Statement';
            const sub = worked ? plural(t.rows, 'row') : t.status === 'your figure' ? 'the fact' : 'first statement held';
            return `<tr>
                <td data-label="${title}"><b>${title === 'Statement' ? '' : 'Your figure '}${esc(day(t.date))}</b><div class="small muted">${esc(sub)}</div></td>
                <td class="r num" data-label="Opening">${worked ? esc(money(t.opening_minor, cur)) : '<span class="muted">none</span>'}</td>
                <td class="r num" data-label="+ money in">${worked ? esc(money(t.in_minor || 0, cur)) : dash}</td>
                <td class="r num" data-label="− money out">${worked ? esc(money(t.out_minor || 0, cur)) : dash}</td>
                <td class="r num" data-label="= works out to">${worked ? `<b>${esc(money(made, cur))}</b>` : dash}</td>
                <td class="r num" data-label="${t.status === 'your figure' ? 'You entered' : 'Statement says'}">${esc(money(t.closing_minor, cur))}</td>
                <td class="tie-check" data-label="Check">${tieTag(t, cur)}</td></tr>`;
        }).join('');
        tieCard = `<section class="card ties-card"><div class="section-header"><h2>Tie lines</h2><span class="section-header__aside">each statement drawn as a sum</span></div>
            ${tieRows ? `<div class="table-shell"><table class="t cells ties-table"><thead><tr><th>Statement</th><th class="r">Opening</th><th class="r">+ money in</th><th class="r">− money out</th><th class="r">= works out to</th><th class="r">Statement says</th><th>Check</th></tr></thead><tbody>${tieRows}</tbody></table></div>`
                : '<p class="card-empty">No balances held.</p>'}</section>`;
    }

    const refusedHTML = refused.refused.map(f => `<div class="item refused acct-refused"><div><div class="what">${icon('prohibit')} ${esc(day(f.statement_date))} statement ${tag(f.set_aside ? 'aside' : 'refused', f.set_aside ? 'refused · set aside' : 'refused')}</div>
        <div class="meta num wrap">${esc(money(f.opening_minor, f.currency))} ${f.rows_minor < 0 ? '−' : '+'} ${plural(f.rows, 'row')} ${esc(money(Math.abs(f.rows_minor), f.currency))} = ${esc(money(f.opening_minor + f.rows_minor, f.currency))}, but it states ${esc(money(f.closing_minor, f.currency))}: <b class="neg">off by ${esc(money(Math.abs(f.difference_minor), f.currency))}</b></div>
        <div class="holds">None of its rows is in the books.</div></div><div></div>
        <div class="act">${f.set_aside ? `<button class="btn sm" data-act="aside" data-id="${f.id}" data-aside="0">Bring it back</button>` : `<button class="btn sm" data-act="aside" data-id="${f.id}" data-aside="1">Known, leave it</button>`}<a class="btn sm" href="#/books/import">Import a fixed file</a></div></div>`).join('');

    const supplied = anchorsList.filter(a => a.source === 'supplied');
    const figures = supplied.map(a => `<li class="fig-row"><span class="fig-row__date">${esc(day(a.date))}</span><span class="fig-row__amt num">${esc(money(a.amount_minor, cur))}</span>
        <span class="fig-row__note">${esc(a.note || '')}</span>
        <span class="fig-row__acts"><button class="btn sm" data-act="figure-fix" data-id="${a.id}" data-date="${a.date}" data-amount="${a.amount_minor}">Correct</button><button class="quiet-word" data-act="figure-delete" data-id="${a.id}" data-date="${a.date}" data-amount="${a.amount_minor}" data-cur="${esc(cur)}">Delete</button></span></li>`).join('');

    // Rows under the statement (or figure) they tie into, each heading with its money in and out.
    const tieOf = new Map();
    ties.ties.forEach(t => (t.row_ids || []).forEach(rid => tieOf.set(rid, t)));
    const newest = ties.ties[0];
    const oldestFirst = [...ties.ties].reverse();
    const groupOf = row => {
        const t = tieOf.get(row.id);
        if (t) return { key: t.date, t };
        if (newest && row.date > newest.date) return { key: 'since', since: newest };
        // A row no tie line counts: under the balance just after it when it
        // falls in that balance's month (a first statement), else on its own.
        const upto = oldestFirst.find(x => x.date >= row.date);
        return upto && daysBetween(row.date, upto.date) <= 31 ? { key: upto.date, t: upto, loose: true } : { key: 'none' };
    };
    let lastKey = null;
    const rowList = rows.transactions.map(row => {
        const g = groupOf(row);
        let headRow = '';
        if (g.key !== lastKey) {
            lastKey = g.key;
            let words;
            if (g.key === 'since') words = `Since the ${g.since.status === 'your figure' ? 'figure' : 'statement'} of ${esc(day(g.since.date))}`;
            else if (g.key === 'none') words = 'Not in a tie line <span class="sec-note">· before the balances held</span>';
            else {
                const t = g.t;
                const kind = t.status === 'your figure' ? 'Up to your figure' : 'Statement';
                const facts = !g.loose && t.opening_minor !== null && t.opening_minor !== undefined
                    ? ` <span class="sec-note">· ${plural(t.rows, 'row')} · in ${esc(money(t.in_minor || 0, cur))} · out ${esc(money(t.out_minor || 0, cur))}</span>` : '';
                words = `${kind} ${esc(day(t.date))}${facts} ${tieTag(t, cur)}`;
            }
            headRow = `<tr class="section"><td colspan="5">${words}</td></tr>`;
        }
        return headRow + acctRowTr(row);
    }).join('');
    ACT.__rows = new Map(rows.transactions.map(x => [x.id, x]));

    return `${booksNav('sheet')}
    <div class="page-head"><div><a class="link small back-link" href="#/books">← Balance sheet</a><span class="eyebrow">Books · ${esc(KIND_WORDS[acct.type] || acct.type)}</span><h1>${esc(acct.name)}${accountMark(id)}</h1>
        <p>${esc(acct.type)} · ${esc(acct.owner)} · kept in ${esc(cur)}${acct.status === 'archived' ? ' · archived' : ''}</p></div>
        <div class="row page-head__end">${acct.takes_a_figure ? `<button class="btn primary" data-act="figure" data-account="${id}">${icon('pencil-simple')}Enter a figure</button>` : ''}${monthPicker()}</div></div>
    <div class="acct-top${tieCard ? '' : ' acct-top--one'}">${hero}${tieCard}</div>
    ${refusedHTML ? `<section class="card"><div class="section-header"><h2>Refused at upload</h2><span class="section-header__aside">a statement that does not tie is kept out whole</span></div>${refusedHTML}</section>` : ''}
    ${figures ? `<section class="card"><div class="section-header"><h2>Your figures</h2><span class="section-header__aside">${plural(supplied.length, 'figure')} · each the fact</span></div><ul class="fig-list">${figures}</ul></section>` : ''}
    <section class="card"><div class="section-header"><h2>Rows</h2><span class="section-header__aside">${plural(rows.total, 'row')} · tap a row for its note, one-off and history</span></div>
        <div class="table-shell"><table class="t rows acct-rows"><thead><tr><th>Date</th><th>Description</th><th>Labelled</th><th class="r">Money in</th><th class="r">Money out</th></tr></thead><tbody>${rowList || '<tr><td colspan="5" class="empty">No rows.</td></tr>'}</tbody></table></div>
        ${pager(rows, 'account-page')}</section>`;
}
ACT['account-page'] = el => { accountPage.page = Number(el.dataset.page); rerender(); window.scrollTo(0, 0); };
function pager(list, actName) {
    if (!list.pages || list.pages <= 1) return '';
    return `<div class="pager"><span class="small muted">page ${list.page} of ${list.pages}</span>
        <button class="btn sm" data-act="${actName}" data-page="${list.page - 1}"${list.page <= 1 ? ' disabled' : ''}>Newer</button>
        <button class="btn sm" data-act="${actName}" data-page="${list.page + 1}"${list.page >= list.pages ? ' disabled' : ''}>Older</button></div>`;
}
function rowTr(row, { account = true, book = false } = {}) {
    const minor = toMinor(row.amount_sgd, row.currency);
    const flowWord = { review: tag('nofig', 'waiting'), transfer: '<span class="muted small">transfer</span>', payment: '<span class="muted small">card payment</span>', movement: `<span class="muted small">movement${row.other_side_name ? ' · ' + esc(row.other_side_name) : ''}</span>`, income: '<span class="muted small">income</span>' }[row.flow_type];
    const label = flowWord || (row.display_type ? esc(row.display_type) : tag('nofig', 'no type'));
    return `<tr class="clickable" data-act="row" data-tx="${row.id}">
        <td class="num">${esc(day(row.date, { year: false }))}</td>
        <td>${esc(row.description)}${rowMark(row.id)}${row.notes ? ` <span class="muted small">· ${esc(row.notes)}</span>` : ''}${row.is_one_off ? ' <span class="tag yours">one-off</span>' : ''}${account ? `<div class="small muted">${esc(row.account_name || '')}</div>` : ''}</td>
        <td>${label}${book || (row.book && row.book !== 'Household') ? ` <span class="small muted">· ${esc(row.book || '')}</span>` : ''}</td>
        <td class="r">${rowAmount(minor, row.currency)}</td></tr>`;
}
ACT['figure-fix'] = async el => {
    const amount = prompt('The corrected figure (what is owed for a loan):', (Math.abs(Number(el.dataset.amount)) / 100).toFixed(2));
    if (amount === null) return;
    const r = await act('PUT', `/api/anchors/${el.dataset.id}`, { amount: amount.replace(/[, ]/g, ''), date: el.dataset.date }, 'Figure corrected');
    if (r) rerender();
};
// Delete is a quiet word on the page; red only here, in its confirm.
ACT['figure-delete'] = el => {
    openSheet('Delete this figure?', `<div class="record-target"><b>${esc(day(el.dataset.date))}</b> · <span class="num">${esc(money(Number(el.dataset.amount), el.dataset.cur || 'SGD'))}</span></div>
        <p>The balance goes back to resting on the figure or statement before it. You can undo this from Changes.</p>`,
    { eyebrow: 'Your figure', center: true,
      foot: `<button class="btn" data-act="close-sheet">Keep it</button><button class="btn danger" data-act="figure-delete-yes" data-id="${esc(el.dataset.id)}">${icon('trash')}Delete the figure</button>` });
};
ACT['figure-delete-yes'] = async el => {
    const r = await act('DELETE', `/api/anchors/${el.dataset.id}`, undefined, 'Figure deleted');
    if (r) { closeSheet(); rerender(); }
};

// ---------------------------------------------------------------------------
// Spending: the old dashboard's views under Books, one book or every book
// with a total each, never one sum. Money out is the total; refunds are
// money back, shown beside it and never taken off it.
// ---------------------------------------------------------------------------

const charts = [];
function totalsByCurrency(rows) {
    const by = {};
    rows.forEach(row => { const c = row.currency || 'SGD'; by[c] = (by[c] || 0) + Math.abs(toMinor(row.amount_sgd, c)); });
    return by;
}
function totalsText(by) {
    const parts = Object.entries(by).map(([c, m]) => money(m, c));
    return parts.length ? parts.join(' and ') : money(0, 'SGD');
}
function groupRows(rows, keyOf) {
    const groups = new Map();
    rows.forEach(row => {
        const k = keyOf(row);
        if (!groups.has(k)) groups.set(k, []);
        groups.get(k).push(row);
    });
    return [...groups.entries()].map(([name, list]) => ({ name, list, by: totalsByCurrency(list), sgd: totalsByCurrency(list).SGD || 0 }))
        .sort((a, b) => b.sgd - a.sgd);
}
/** A refund: money back on a spending row (a negative amount). */
function isRefund(row) { return toMinor(row.amount_sgd, row.currency) < 0; }
/** The months the coverage list must reach back to hold `from`, counted to today. */
function monthsSince(from) {
    let n = 1, m = currentMonth();
    while (m > from && n < 40) { m = prevMonth(m); n += 1; }
    return n;
}
/** The filter state, with the old single type read as a list of one. */
function spendState() {
    const sp = S.spending;
    if (!Array.isArray(sp.types)) sp.types = sp.type ? [sp.type] : [];
    delete sp.type;
    if (sp.fold === undefined) sp.fold = false;
    return sp;
}
function typeWord(name, r) {
    if (name === '__untyped__') return 'No type';
    const t = r.types.find(x => x.name === name);
    return t ? (t.display_name || t.name) : name;
}
/** The filters folded behind "Filters" on a phone, each a removable chip. */
function activeSpendFilters(sp, r) {
    const out = sp.types.map(t => ({ label: typeWord(t, r), k: 'type', v: t }));
    if (sp.search) out.push({ label: `“${sp.search}”`, k: 'search' });
    if (sp.account) out.push({ label: r.accountById.get(Number(sp.account))?.name || 'one account', k: 'account' });
    if (!sp.oneOffs) out.push({ label: 'no one-offs', k: 'oneOffs' });
    if (sp.fold) out.push({ label: 'sub-types folded', k: 'fold' });
    return out;
}
/** Search, types, account, one-offs and the sub-type fold: inline on the desk, in a sheet on a phone. */
function spendFilterFields(sp, r) {
    const accounts = r.accounts.filter(a => ['bank', 'card'].includes(a.type));
    const chosen = sp.types.map(t => `<button type="button" class="chip" aria-pressed="true" data-act="spend-type-drop" data-v="${esc(t)}" aria-label="Remove ${esc(typeWord(t, r))}">${esc(typeWord(t, r))} ${icon('x')}</button>`).join('');
    const left = r.types.filter(t => t.kind === 'spending' && !sp.types.includes(t.name));
    return `<div class="sp-fields">
        <label class="field sp-search"><span>Search</span><input type="search" data-sp-search value="${esc(sp.search)}" placeholder="Merchant, description or type"></label>
        <div class="field sp-types"><span class="sp-label">Types</span><div class="chips">${chosen}
            <label class="sp-add"><span class="sr-only">Add a type</span><select data-change="spend-type-add"><option value="">${sp.types.length ? '+ add a type' : 'Every type'}</option>
                ${sp.types.includes('__untyped__') ? '' : '<option value="__untyped__">No type</option>'}${left.map(t => `<option value="${esc(t.name)}">${esc(t.display_name || t.name)}</option>`).join('')}</select></label></div></div>
        <label class="field sp-account"><span>Account</span><select data-change="spend-account"><option value="">Every account</option>${accounts.map(a => `<option value="${a.id}"${String(a.id) === String(sp.account) ? ' selected' : ''}>${esc(a.name)}</option>`).join('')}</select></label>
        <div class="sp-checks"><label class="check"><input type="checkbox" data-change="spend-oneoffs"${sp.oneOffs ? ' checked' : ''}> Include one-offs</label>
            <label class="check"><input type="checkbox" data-change="spend-fold"${sp.fold ? ' checked' : ''}> Fold sub-types into their parent</label></div>
    </div>`;
}
/** A spending row: no money-out pill (every row here is money out); a refund says so. */
function spendRowTr(row) {
    const minor = toMinor(row.amount_sgd, row.currency);
    const label = row.display_type ? esc(row.display_type) : tag('nofig', 'no type');
    const amount = minor < 0
        ? `<span class="sl-refund">${icon('arrow-circle-down')}refund</span> <span class="num sl-back">${esc(money(-minor, row.currency))} back</span>`
        : `<span class="num">${esc(money(minor, row.currency))}</span>`;
    return `<tr class="clickable" data-act="row" data-tx="${row.id}">
        <td class="num">${esc(day(row.date, { year: false }))}</td>
        <td>${esc(row.description)}${rowMark(row.id)}${row.notes ? ` <span class="muted small">· ${esc(row.notes)}</span>` : ''}${row.is_one_off ? ' <span class="tag yours">one-off</span>' : ''}<div class="small muted">${esc(row.account_name || '')}</div></td>
        <td>${label}</td>
        <td class="r">${amount}</td></tr>`;
}

async function viewSpending() {
    const sp = spendState();
    const r = await refs();
    const months = monthsBack(S.month, 12);
    const span = sp.span === 'year' ? months : [S.month];
    const household = r.books[0].name;
    const books = sp.book === 'all' ? r.books.map(b => b.name) : [sp.book];
    const qs = book => {
        const p = new URLSearchParams({ book, start: months[0] + '-01', end: monthEnd(S.month), expense_only: 'true', sort: 'date', sort_dir: 'desc' });
        if (sp.search) p.set('search', sp.search);
        if (sp.types.length) p.set('types', sp.types.join(','));
        if (sp.account) p.set('account_id', sp.account);
        if (!sp.oneOffs) p.set('exclude_one_off', 'true');
        return p.toString();
    };
    // Every page is read: a total is never one page's sum.
    const [lists, coverage] = await Promise.all([
        Promise.all(books.map(b => getAllRows(qs(b)))),
        get(`/api/statements/coverage?months=${monthsSince(months[0])}`).catch(() => null),
    ]);
    const perBook = books.map((book, i) => {
        const year = lists[i].transactions;
        const inSpan = year.filter(row => span.includes(row.date.slice(0, 7)));
        const rows = inSpan.filter(row => !isRefund(row));
        const back = inSpan.filter(isRefund);
        const others = [...new Set(year.map(row => row.currency || 'SGD').filter(c => c !== 'SGD'))];
        return { book, year, rows, back, all: inSpan, by: totalsByCurrency(rows), backBy: totalsByCurrency(back), short: shownOf(lists[i]), others };
    });
    ACT.__rows = new Map(perBook.flatMap(b => b.all).map(x => [x.id, x]));
    const spanWords = span.length === 1 ? `${monthName(S.month)}${S.month === currentMonth() ? ' so far' : ''}` : `12 months to ${monthName(S.month, { short: true })}`;

    // P1.3: a month with nothing in it says why, and where the latest rows are.
    let emptyNote = '';
    if (sp.span !== 'year' && perBook.every(b => !b.all.length)) {
        const latest = perBook.flatMap(b => b.year).map(x => x.date).sort().pop();
        const filtered = activeSpendFilters(sp, r).length;
        emptyNote = `<div class="notice sl-empty-month">${icon('calendar-blank')}<div class="grow"><b>Nothing in ${esc(monthName(S.month))}${S.month === currentMonth() ? ' yet' : ''}.</b>
            ${latest ? `The latest rows are from ${esc(day(latest, { year: latest.slice(0, 4) !== S.month.slice(0, 4) }))}${S.month === currentMonth() ? `; ${esc(LONG_MONTHS[Number(S.month.slice(5)) - 1])}’s statements are not in` : ''}.` : filtered ? 'Nothing matches the filters in the 12 months either.' : 'No statement for it is in.'}</div>
            ${latest ? `<button class="btn" data-act="spend-month" data-m="${latest.slice(0, 7)}">Look at ${esc(LONG_MONTHS[Number(latest.slice(5, 7)) - 1])}</button>` : ''}</div>`;
    }

    // P1.1, P1.2: one tile per book; money out is its total, refunds beside it.
    const tiles = `<div class="stat-tiles sl-tiles">${perBook.map(b => {
        const isHouse = b.book === household;
        return `<div class="stat-tile${isHouse ? '' : ' hatch'}">
            <div class="sl-tile-head"><span class="stat-tile__label">${isHouse ? 'Household spending' : `${esc(b.book)} · its costs`}</span>${isHouse ? '' : '<span class="outside-tag">not household</span>'}</div>
            <div class="stat-tile__figure">${esc(totalsText(b.by))}</div>
            <div class="stat-tile__sub">${plural(b.rows.length, 'row')} out · ${esc(spanWords)}</div>
            ${b.back.length ? `<div class="sl-back-line"><span class="delta-chip delta-chip--up">${icon('arrow-circle-down')}back in refunds ${esc(totalsText(b.backBy))}</span><span class="muted small">${plural(b.back.length, 'row')}, not taken off</span></div>` : ''}
            ${b.short ? `<p class="small sl-short">${icon('warning')}Short: too many rows to read at once (${esc(b.short)} over 12 months). Narrow the filters.</p>` : ''}</div>`;
    }).join('')}</div>
        <p class="card-foot">${perBook.length > 1 ? 'Each book has its own total. They are never added together. ' : ''}Refunds are shown beside spending, never taken off it.</p>`;

    // P3.1: a month with no statement in for this book's accounts is marked, not drawn as nothing spent.
    const missingFor = b => {
        if (!coverage) return new Set();
        const ids = new Set(b.year.map(x => x.account_id).filter(id => coverage.matrix[id]));
        const out = new Set();
        if (!ids.size) return out;
        months.forEach(m => {
            if (m >= currentMonth()) return;
            if (b.year.some(x => x.date.startsWith(m))) return;
            if ([...ids].some(id => coverage.matrix[id][m] && !coverage.matrix[id][m].imported)) out.add(m);
        });
        return out;
    };
    const fold = sp.fold;
    const typeKey = row => (fold ? (row.parent_type || row.type) : row.display_type) || 'No type';
    const body = perBook.map(b => {
        // P1.4: a book with no costs in the 12 months folds to one line.
        if (!b.year.length) {
            return `<section class="card sl-folded"><h2>${esc(b.book)}</h2><p>No costs in the 12 months to ${esc(monthName(S.month, { short: true }))}${activeSpendFilters(sp, r).length ? ' that match the filters' : ''}, so there is no chart.</p>
                <a class="link" href="#/books">Look at ${esc(b.book)} on the balance sheet</a></section>`;
        }
        b.missing = missingFor(b);
        let content;
        if (sp.view === 'flat') {
            const list = b.all;
            content = list.length ? `<div class="table-wrap"><table class="t rows sl-rows"><thead><tr><th>Date</th><th>Description</th><th>Type</th><th class="r">Amount</th></tr></thead>
                <tbody>${list.slice(0, 300).map(spendRowTr).join('')}</tbody></table></div>
                <p class="card-foot">Every row here is money out unless it says refund.${list.length > 300 ? ` The first 300 of ${list.length} rows; narrow the filters to see the rest.` : ''}</p>`
                : `<p class="card-empty">No rows in ${esc(spanWords)}.</p>`;
        } else {
            const keyOf = sp.view === 'type' ? typeKey : row => row.service_name || row.description;
            const groups = groupRows(b.rows, keyOf);
            const backs = groupRows(b.back, keyOf);
            const noun = sp.view === 'type' ? 'Type' : 'Merchant';
            const nameCell = g => g.name === 'No type' ? tag('nofig', 'No type') : esc(g.name);
            content = groups.length || backs.length ? `<div class="table-wrap"><table class="t sl-groups"><thead><tr><th>${noun}</th><th class="r">Rows</th><th class="r">Total</th><th class="r desk-only">Share</th></tr></thead><tbody>
                ${groups.slice(0, 60).map(g => `<tr class="clickable" data-act="spend-drill" data-key="${esc(g.name)}" data-view="${sp.view}"><td>${nameCell(g)}</td><td class="r num">${g.list.length}</td>
                    <td class="r num">${esc(totalsText(g.by))}</td><td class="r num desk-only">${pct(g.sgd, b.by.SGD || 0)}</td></tr>`).join('')}
                ${groups.length > 60 ? `<tr><td colspan="4" class="muted">…and ${groups.length - 60} more</td></tr>` : ''}
                ${backs.length ? `<tr class="section"><td colspan="4">Back in: refunds, not spending</td></tr>
                    ${backs.map(g => `<tr class="clickable" data-act="spend-drill" data-key="${esc(g.name)}" data-view="${sp.view}"><td>${nameCell(g)}</td><td class="r num">${g.list.length}</td>
                        <td class="r num sl-back">${esc(totalsText(g.by))} back</td><td class="r small muted desk-only">not in the share</td></tr>`).join('')}` : ''}
                </tbody></table></div>`
                : `<p class="card-empty">No rows in ${esc(spanWords)}.</p>`;
        }
        const missingWords = b.missing.size ? ` A hatched month has no statement in for some of this book’s accounts; <a class="link" href="#/books/import">import it</a>.` : '';
        return `<section class="card"><div class="section-header"><h2>${esc(b.book)}</h2>
                <span class="section-header__aside">out <b class="num">${esc(totalsText(b.by))}</b>${b.back.length ? ` · back <b class="num">${esc(totalsText(b.backBy))}</b>` : ''} · ${plural(b.all.length, 'row')}</span></div>
            <div class="chart-box small sl-chart"><canvas id="chart-${esc(b.book)}" aria-label="${esc(b.book)}, money out by month"></canvas></div>
            <p class="small muted sl-chart-note">Money out by month, 12 months to ${esc(monthName(S.month, { short: true }))}, on its own scale. Tap a month to look at it.
                ${b.others.length ? `<b>S$ rows only</b>; ${esc(b.others.map(c => SIGNS[c] || c).join(' and '))} rows are shown in the table and its total, not in the chart.` : 'In S$.'}${missingWords}</p>
            ${content}</section>`;
    }).join('');

    afterRender(() => drawSpendingCharts(perBook, months));
    const active = activeSpendFilters(sp, r);
    const seg = (label, k, opts) => `<div class="sp-group"><span class="sp-label">${label}</span><div class="seg" role="group" aria-label="${label}">${opts.map(([v, l]) =>
        `<button type="button" data-act="spend-set" data-k="${k}" data-v="${esc(v)}" aria-pressed="${String(sp[k] === v)}">${esc(l)}</button>`).join('')}</div></div>`;
    return `${booksNav('spending')}
    <div class="page-head"><div><span class="eyebrow">Books</span><h1>Spending</h1><p>Household spending and each company’s costs, a book at a time or every book with its own total.</p></div></div>
    <section class="card sp-filterbar">
        <div class="sp-row">
            <div class="sp-group sp-period"><span class="sp-label">Period</span><div class="sp-period-row">
                <label class="sp-month"><span class="sr-only">Month</span><select data-change="month">${sheetMonths().map(m => `<option value="${m}"${m === S.month ? ' selected' : ''}>${monthName(m, { short: true })}${m === currentMonth() ? ' (so far)' : ''}</option>`).join('')}</select></label>
                <div class="seg" role="group" aria-label="Period">${[['month', 'Month'], ['year', '12 months']].map(([v, l]) => `<button type="button" data-act="spend-set" data-k="span" data-v="${v}" aria-pressed="${String(sp.span === v)}">${l}</button>`).join('')}</div></div></div>
            ${seg('Book', 'book', [['all', 'All'], ...r.books.map(b => [b.name, b.name])])}
            ${seg('View', 'view', [['flat', 'Every row'], ['merchant', 'By merchant'], ['type', 'By type']])}
        </div>
        <div class="sp-more">${spendFilterFields(sp, r)}${active.length ? '<button type="button" class="link sp-clear" data-act="spend-clear">Clear filters</button>' : ''}</div>
        <div class="sp-fold-row">
            <button type="button" class="btn subtle" data-act="spend-filters" aria-haspopup="dialog">${icon('funnel')}Filters${active.length ? ` <span class="count">${active.length}</span>` : ''}</button>
            ${active.map(f => `<button type="button" class="chip" data-act="spend-drop" data-k="${f.k}" data-v="${esc(f.v || '')}" aria-label="Remove ${esc(f.label)}">${esc(f.label)} ${icon('x')}</button>`).join('')}
        </div>
    </section>
    ${emptyNote}
    <section class="card">${tiles}</section>
    ${body}`;
}
function setSpending(k, v) {
    S.spending[k] = v; store.set('spending', S.spending); rerender();
    if ($('#sheets .sp-sheet')) setTimeout(openSpendFilters, 0);   // the sheet shows what was just set
}
ACT['spend-set'] = el => setSpending(el.dataset.k, el.dataset.v);
ACT['spend-month'] = el => { S.month = el.dataset.m; store.set('month', S.month); rerender(); };
ACT['spend-type-add'] = el => { if (el.value) setSpending('types', [...spendState().types, el.value]); };
ACT['spend-type-drop'] = el => setSpending('types', spendState().types.filter(t => t !== el.dataset.v));
ACT['spend-account'] = el => setSpending('account', el.value);
ACT['spend-oneoffs'] = el => setSpending('oneOffs', el.checked);
ACT['spend-fold'] = el => setSpending('fold', el.checked);
ACT['spend-drop'] = el => {
    const k = el.dataset.k;
    if (k === 'type') setSpending('types', spendState().types.filter(t => t !== el.dataset.v));
    else if (k === 'search') setSpending('search', '');
    else if (k === 'account') setSpending('account', '');
    else if (k === 'oneOffs') setSpending('oneOffs', true);
    else if (k === 'fold') setSpending('fold', false);
};
ACT['spend-clear'] = () => {
    Object.assign(S.spending, { types: [], search: '', account: '', oneOffs: true, fold: false });
    store.set('spending', S.spending); rerender();
    if ($('#sheets .sp-sheet')) setTimeout(openSpendFilters, 0);
};
ACT['spend-filters'] = () => openSpendFilters();
async function openSpendFilters() {
    const r = await refs();
    const sp = spendState();
    openSheet('Filters', `<div class="sp-sheet">${spendFilterFields(sp, r)}</div>`, { eyebrow: 'Spending',
        foot: '<button type="button" class="btn" data-act="spend-clear">Clear every filter</button><button type="button" class="btn primary" data-act="close-sheet">Done</button>' });
}
ACT['spend-drill'] = el => {
    const sp = spendState();
    if (el.dataset.view === 'type') { sp.types = [el.dataset.key === 'No type' ? '__untyped__' : el.dataset.key]; sp.search = ''; }
    else { sp.search = el.dataset.key; }
    sp.view = 'flat'; store.set('spending', S.spending); rerender();
};
document.addEventListener('keydown', e => {
    if (e.key === 'Enter' && e.target.matches && e.target.matches('[data-sp-search]')) setSpending('search', e.target.value.trim());
});
document.addEventListener('search', e => { if (e.target.matches && e.target.matches('[data-sp-search]')) setSpending('search', e.target.value.trim()); }, true);

/** A hatch for a month with no statement in: the warning tone in stripes. */
function hatchPattern(color, ground) {
    const c = document.createElement('canvas');
    c.width = 8; c.height = 8;
    const g = c.getContext('2d');
    g.fillStyle = ground; g.fillRect(0, 0, 8, 8);
    g.strokeStyle = color; g.lineWidth = 2;
    g.beginPath(); g.moveTo(-2, 10); g.lineTo(10, -2); g.moveTo(-2, 2); g.lineTo(2, -2); g.moveTo(6, 10); g.lineTo(10, 6); g.stroke();
    return g.createPattern(c, 'repeat');
}
function drawSpendingCharts(perBook, months) {
    charts.splice(0).forEach(c => c.destroy());
    if (typeof Chart === 'undefined') return;
    const tk = chartTokens();
    const st = getComputedStyle(document.documentElement);
    const warn = st.getPropertyValue('--semantic-warning').trim();
    const narrow = window.innerWidth < 560;
    perBook.forEach((b, i) => {
        const canvas = document.getElementById(`chart-${b.book}`);
        if (!canvas) return;
        const missing = b.missing || new Set();
        const sums = months.map(m => b.year.filter(row => row.date.startsWith(m) && (row.currency || 'SGD') === 'SGD' && !isRefund(row))
            .reduce((a, row) => a + toMinor(row.amount_sgd, 'SGD'), 0) / 100);
        const top = Math.max(...sums, 1);
        const fill = tk.books[i % tk.books.length];
        const label = m => MONTHS[Number(m.slice(5)) - 1];
        charts.push(new Chart(canvas, {
            type: 'bar',
            data: { labels: months.map(m => missing.has(m) ? [label(m), narrow ? '⧗' : '⧗ missing'] : label(m)), datasets: [
                { label: `${b.book}, money out`, data: sums,
                    backgroundColor: months.map(m => m === S.month ? tk.current : fill), borderRadius: 4, barPercentage: 0.64, categoryPercentage: 1 },
                { label: 'no statement in', data: months.map(m => missing.has(m) ? top : null),
                    backgroundColor: hatchPattern(tk.base, tk.surface), borderColor: tk.base, borderWidth: 1, borderRadius: 4, barPercentage: 0.64, categoryPercentage: 1 },
            ] },
            options: {
                responsive: true, maintainAspectRatio: false, animation: false,
                plugins: { legend: { display: false }, tooltip: { callbacks: { label: c => c.datasetIndex === 1 ? 'No statement in yet for this month: import it' : money(Math.round(c.raw * 100), 'SGD') } } },
                scales: {
                    x: { stacked: true, grid: { display: false }, border: { color: tk.base },
                        ticks: { font: { family: tk.mono, size: narrow ? 9 : 10 }, maxRotation: 0, autoSkip: false, color: c => missing.has(months[c.index]) ? warn : tk.ink } },
                    y: { stacked: true, border: { display: false }, grid: { color: tk.grid, lineWidth: 1 }, ticks: { color: tk.ink, font: { family: tk.mono, size: 10 }, maxTicksLimit: 5, callback: v => `S$ ${v >= 1000 ? (v / 1000) + 'k' : v}` } },
                },
                onClick: (_e, els) => {
                    if (!els.length) return;
                    const m = months[els[0].index];
                    if (missing.has(m)) { location.hash = '#/books/import'; return; }
                    S.month = m; store.set('month', S.month); rerender();
                },
            },
        }));
    });
}

// ---------------------------------------------------------------------------
// Bills: the subscriptions, under Spending. A missed renewal is a queue item.
// Grouped by book, each with its own monthly line; missed bills first.
// ---------------------------------------------------------------------------

function billEvery(s) {
    return s.periods > 1 ? `every ${s.periods} ${s.frequency.replace('ly', '').replace('month', 'months').replace('year', 'years').replace('quarter', 'quarters')}` : s.frequency;
}
function billMissedTag(s, missed) {
    return `<button type="button" class="tag stale" data-act="bill-queue" data-id="${s.id}" title="Open it in the queue">${icon('hourglass')}no payment seen for ${esc(day(missed.due, { year: false }))}</button>`;
}
async function viewBills() {
    const [subs, r] = await Promise.all([get('/api/subscriptions'), refs()]);
    ACT.__subs = new Map(subs.map(s => [s.id, s]));
    const household = r.books[0].name;
    const byBook = new Map(r.books.map(b => [b.name, []]));
    subs.forEach(s => { const b = s.book || household; if (!byBook.has(b)) byBook.set(b, []); byBook.get(b).push({ s, missed: billMissed(s) }); });
    const statusOrder = { active: 0, paused: 1, deactivated: 2 };
    const row = ({ s, missed }) => {
        const cur = s.currency || 'SGD';
        const name = esc(s.service_name || s.match_pattern);
        const state = missed ? billMissedTag(s, missed) : s.status !== 'active' ? tag('aside', s.status === 'deactivated' ? 'stopped' : s.status) : '';
        const sub = missed ? billMissedTag(s, missed)
            : `${esc(billEvery(s))}${s.status === 'active' ? ` · next ${esc(day(s.computed_renewal, { year: false }))}` : ''}${s.account_name ? ' · ' + esc(s.account_name) : ''} ${s.status !== 'active' ? state : ''}`;
        return `<tr class="clickable${s.status === 'active' ? '' : ' sl-inactive'}" data-act="bill" data-id="${s.id}">
            <td class="sl-name"><b>${name}</b><div class="small muted">${esc(s.display_type || 'no type')}</div>${state ? `<div class="sl-state">${state}</div>` : ''}</td>
            <td class="r num sl-fig">${esc(money(toMinor(s.amount, cur), cur))}${s.is_variable ? '<div class="small muted">varies</div>' : ''}</td>
            <td>${esc(billEvery(s))}</td>
            <td class="r num">${esc(money(toMinor(s.monthly_sgd, 'SGD'), 'SGD'))}</td>
            <td class="small">${esc(s.account_name || '')}</td>
            <td class="num">${esc(day(s.tx_last_paid || s.last_paid))}</td>
            <td class="num">${s.status === 'active' ? esc(day(s.computed_renewal)) : '—'}</td>
            <td class="sl-sub">${sub}</td></tr>`;
    };
    const groups = [...byBook.entries()].map(([book, list]) => {
        const active = list.filter(x => x.s.status === 'active');
        const monthly = active.reduce((a, x) => a + toMinor(x.s.monthly_sgd, 'SGD'), 0);
        const missed = list.filter(x => x.missed).sort((a, b) => a.missed.due.localeCompare(b.missed.due));
        const paid = list.filter(x => !x.missed).sort((a, b) => (statusOrder[a.s.status] ?? 3) - (statusOrder[b.s.status] ?? 3)
            || String(a.s.computed_renewal || '').localeCompare(String(b.s.computed_renewal || '')));
        const head = `<tr class="section sl-book"><td colspan="5" class="sl-name">${esc(book)}</td>
            <td colspan="3" class="r sl-fig">${esc(money(monthly, 'SGD'))} a month · ${list.length ? (missed.length ? `${missed.length} of ${list.length} not seen` : plural(list.length, 'bill')) : 'no bills'}</td></tr>`;
        return head + missed.map(row).join('')
            + (missed.length && paid.length ? '<tr class="section sl-sub-head"><td colspan="8" class="sl-name">Paid as expected</td></tr>' : '')
            + paid.map(row).join('');
    }).join('');
    return `${booksNav('bills')}
    <div class="page-head"><div><span class="eyebrow">Books</span><h1>Bills</h1><p>What renews and when, from the rows that pay it. A renewal with no payment seen also waits in the queue.</p></div>
        <div class="row"><button class="btn" data-act="bills-refresh">${icon('arrows-clockwise')}Refresh from rows</button><button class="btn primary" data-act="bill" data-id="">${icon('plus')}Add a bill</button></div></div>
    <section class="card"><div class="table-wrap"><table class="t sl-stack sl-bills"><thead><tr><th>Bill</th><th class="r">Amount</th><th>How often</th><th class="r">A month</th><th>Paid from</th><th>Last paid</th><th>Next</th></tr></thead>
        <tbody>${subs.length ? groups : '<tr><td colspan="8" class="empty sl-name">No bills yet.</td></tr>'}</tbody></table></div>
        <p class="card-foot">Monthly figures in S$ at each bill’s own rate. Each book’s bills are their own total; no line adds the books.</p></section>`;
}
ACT.bill = async el => {
    const [r, services] = await Promise.all([refs(), servicesList()]);
    const s = el.dataset.id ? ACT.__subs.get(Number(el.dataset.id)) : null;
    const missed = s ? billMissed(s) : null;
    const sel = (id, opts, v) => `<select id="${id}">${opts.map(([k, l]) => `<option value="${esc(k)}"${String(k) === String(v ?? '') ? ' selected' : ''}>${esc(l)}</option>`).join('')}</select>`;
    const paid = s && (s.tx_last_paid || s.last_paid);
    const missedNote = missed ? `<div class="notice warn sl-missed">${icon('hourglass')}<div class="grow"><b>No payment seen for ${esc(day(missed.due))}.</b>
        <div class="small">${paid ? `Last paid ${esc(money(toMinor(s.tx_amount ?? s.amount, s.currency || 'SGD'), s.currency || 'SGD'))} on ${esc(day(paid))}${s.account_name ? ` from ${esc(s.account_name)}` : ''}.` : 'No payment seen yet.'}</div>
        <div class="row sl-missed-acts"><button type="button" class="btn sm" data-act="bill-stopped" data-id="${s.id}">It has stopped</button>
            <button type="button" class="btn sm" data-act="bill-find" data-id="${s.id}">Find the payment</button>
            <button type="button" class="link" data-act="bill-queue" data-id="${s.id}">Open it in the queue</button></div></div></div>` : '';
    openSheet(s ? esc(s.service_name || 'Bill') : 'Add a bill', `${missedNote}<div class="fields">
        <label class="field"><span>Merchant</span>${sel('bl-svc', [['', 'Choose'], ...services.map(x => [x.id, x.name])], s?.service_id)}</label>
        <div class="fields two"><label class="field"><span>Amount</span><input type="text" inputmode="decimal" id="bl-amount" value="${s ? esc(s.amount) : ''}"></label>
            <label class="field"><span>Currency</span>${sel('bl-cur', ['SGD', 'USD', 'INR', 'EUR', 'GBP', 'AUD'].map(c => [c, c]), s?.currency || 'SGD')}</label></div>
        <div class="fields two"><label class="field"><span>How often</span>${sel('bl-freq', [['monthly', 'monthly'], ['quarterly', 'quarterly'], ['yearly', 'yearly']], s?.frequency || 'monthly')}</label>
            <label class="field"><span>Every how many</span><input type="number" min="1" id="bl-periods" value="${s ? s.periods : 1}"></label></div>
        <label class="field"><span>Paid from</span>${sel('bl-acct', [['', '—'], ...r.accounts.filter(a => ['bank', 'card'].includes(a.type)).map(a => [a.id, a.name])], s?.account_id)}</label>
        <div class="fields two"><label class="field"><span>Counts from</span><input type="date" id="bl-renew" value="${esc(s?.renewal_date || '')}"></label>
            ${s && s.status === 'active' && s.computed_renewal ? `<div class="field"><span class="sp-label">Next renewal</span><div class="sl-readonly num">${esc(day(s.computed_renewal))}</div></div>` : ''}</div>
        <label class="field"><span>State</span>${sel('bl-status', [['active', 'active'], ['paused', 'paused'], ['deactivated', 'stopped']], s?.status || 'active')}</label>
        <label class="field"><span>Matches rows containing</span><input type="text" id="bl-pattern" value="${esc(s?.match_pattern || '')}"></label>
        <label class="field"><span>Where to manage it</span><input type="url" id="bl-link" value="${esc(s?.link || '')}"></label>
        <label class="field"><span>Note</span><input type="text" id="bl-notes" value="${esc(s?.notes || '')}"></label></div>`,
    { eyebrow: s ? 'Bill' : 'Bills', foot: `${s ? `<button class="btn danger" data-act="bill-delete" data-id="${s.id}">${icon('trash')}Delete</button>` : ''}<button class="btn primary" data-act="bill-save" data-id="${s ? s.id : ''}">Save</button>` });
};
ACT['bill-stopped'] = async el => {
    const r = await act('PUT', `/api/subscriptions/${el.dataset.id}`, { status: 'deactivated' }, 'Bill marked stopped');
    if (r) { closeSheet(); rerender(); }
};
ACT['bill-find'] = el => {
    const s = ACT.__subs.get(Number(el.dataset.id));
    const missed = billMissed(s);
    const sp = spendState();
    Object.assign(sp, { book: s.book || 'all', view: 'flat', span: 'month', search: s.match_pattern || s.service_name || '', types: [], account: '', oneOffs: true });
    store.set('spending', S.spending);
    if (missed) { S.month = missed.due.slice(0, 7) > currentMonth() ? currentMonth() : missed.due.slice(0, 7); store.set('month', S.month); }
    closeSheet();
    location.hash = '#/books/spending';
};
// That bill's own queue item: the queue opened in full, the item brought into view.
ACT['bill-queue'] = el => {
    const s = ACT.__subs.get(Number(el.dataset.id));
    const title = `${s.service_name || s.match_pattern}: no payment seen`;
    queueOpen.out = 9999;
    afterNextRender = () => {
        const item = $$('#view article.item.bill').find(a => ($('.what', a)?.textContent || '').trim().startsWith(title));
        if (item) { item.setAttribute('tabindex', '-1'); item.scrollIntoView({ block: 'center' }); item.focus({ preventScroll: true }); }
    };
    closeSheet();
    location.hash = '#/queue';
};
// Each bill's last payment from the rows that match it, and its renewal moved
// on past a payment seen.
ACT['bills-refresh'] = async el => {
    el.disabled = true;
    const r = await act('POST', '/api/subscriptions/enrich', {}, 'Bills refreshed from rows');
    el.disabled = false;
    if (r) { toast(`${plural(r.data.updated || 0, 'bill')} refreshed${r.data.renewals_advanced ? `, ${r.data.renewals_advanced} renewal${r.data.renewals_advanced === 1 ? '' : 's'} moved on` : ''}`); rerender(); }
};
ACT['bill-save'] = async el => {
    const v = id => $('#' + id).value.trim();
    if (!v('bl-svc')) { toast('Choose the merchant', { bad: true }); return; }
    const body = { service_id: Number(v('bl-svc')), amount: Number(v('bl-amount') || 0), currency: v('bl-cur'), frequency: v('bl-freq'),
        periods: Number(v('bl-periods') || 1), account_id: v('bl-acct') ? Number(v('bl-acct')) : null, renewal_date: v('bl-renew') || null,
        status: v('bl-status'), match_pattern: v('bl-pattern') || null, link: v('bl-link') || null, notes: v('bl-notes') || null };
    const r = el.dataset.id ? await act('PUT', `/api/subscriptions/${el.dataset.id}`, body, 'Bill saved') : await act('POST', '/api/subscriptions', body, 'Bill added');
    if (r) { closeSheet(); rerender(); }
};
ACT['bill-delete'] = async el => {
    if (!confirm('Delete this bill? You can undo it from Changes.')) return;
    const r = await act('DELETE', `/api/subscriptions/${el.dataset.id}`, undefined, 'Bill deleted');
    if (r) { closeSheet(); rerender(); }
};

// ---------------------------------------------------------------------------
// Lists: types, merchants (clean-up), rules (edit), accounts (add, a figure)
// On a phone every list is stacked rows: never a sideways table.
// ---------------------------------------------------------------------------

const MATCH_WORDS = { contains: 'contains', startswith: 'starts with', exact: 'exactly' };
/** Words written for the code, said plainly on the page. */
function plainWords(s) {
    return String(s || '').replace(/\s*\(ticket \d+\)/gi, '').replace(/the operator has not placed/gi, 'you have not placed yet').replace(/the operator/gi, 'you');
}
const listState = { search: '', filter: 'all' };
async function viewLists(tab) {
    const tabs = [['merchants', 'Merchants', 'coins'], ['rules', 'Rules', 'sliders-horizontal'], ['accounts', 'Accounts', 'bank'], ['rates', 'Rates', 'arrows-left-right'], ['types', 'Types', 'list-bullets']];
    const head = `${booksNav('lists')}<div class="page-head"><div><span class="eyebrow">Books</span><h1>Lists</h1><p>What the rules and labels are made of. Every change here lands in Changes, with Undo.</p></div></div>
        <nav class="seg sl-tabs" aria-label="Lists">${tabs.map(([k, l, ic]) => `<a href="#/books/lists/${k}"${k === tab ? ' aria-current="page"' : ''}>${icon(ic)}${l}</a>`).join('')}</nav>`;
    const search = placeholder => `<label class="field sl-search"><span class="sr-only">Search</span><input type="search" id="ls-search" value="${esc(listState.search)}" placeholder="${placeholder}"></label>`;
    const r = await refs();
    const needle = listState.search.toLowerCase();

    if (tab === 'types') {
        const income = await get('/api/types?kind=income').catch(() => []);
        const row = t => `<tr><td class="sl-name"><b>${esc(t.display_name || t.name)}</b></td><td class="small">${esc(plainWords(t.covers))}</td>
            <td class="small muted">${esc(plainWords(t.not_for || ''))}</td><td class="small">${t.kind === 'income' ? '' : `${esc(t.proposed_book || 'any book')}${t.default_one_off ? ', one-off by default' : ''}`}</td>
            <td class="sl-sub">${esc(plainWords(t.covers))}${t.kind === 'income' ? '' : ` · ${esc(t.proposed_book || 'any book')}${t.default_one_off ? ', one-off by default' : ''}`}${t.not_for ? `<div>Not for: ${esc(plainWords(t.not_for))}</div>` : ''}</td></tr>`;
        return `${head}<section class="card"><div class="section-header"><div><h2>Types</h2><p>One list serves every book. To add or rename a type, ask Claude in chat.</p></div></div>
            <div class="table-wrap"><table class="t sl-stack"><thead><tr><th>Type</th><th>Covers</th><th>Not for</th><th>Usual book</th></tr></thead><tbody>${r.types.filter(t => t.kind === 'spending').map(row).join('')}</tbody></table></div>
            ${income.length ? `<h3 class="sl-subhead">Income kinds</h3><div class="table-wrap"><table class="t sl-stack"><thead><tr><th>Kind</th><th>Covers</th><th>Not for</th><th></th></tr></thead><tbody>${income.map(row).join('')}</tbody></table></div>`
                : '<p class="card-foot">Income kinds: none set up yet.</p>'}</section>`;
    }
    if (tab === 'rates') {
        const saved = await get('/api/rates');
        const foreign = [...new Set(r.accounts.filter(a => a.currency && a.currency !== 'SGD').map(a => a.currency))];
        const rows = saved.slice(0, 200).map(x => `<tr><td class="num sl-name">${esc(day(x.date))}</td><td>${esc(x.pair)}</td><td class="r num sl-fig">${esc(x.rate)}</td><td class="small muted">${esc(plainWords(x.source))}</td>
            <td class="sl-sub">${esc(x.pair)}${x.source ? ' · ' + esc(plainWords(x.source)) : ''}</td>
            <td class="r sl-end"><button class="btn sm" data-act="rate" data-currency="${esc(String(x.pair).split('/')[0])}" data-date="${esc(x.date)}">Change</button></td></tr>`).join('');
        return `${head}<section class="card"><div class="section-header"><div><h2>Rates</h2><p>S$ for one unit, by day. A balance in another currency joins the S$ totals at the rate for its day, or the latest saved before it.</p></div>
            <div class="row">${(foreign.length ? foreign : ['INR']).map(c => `<button class="btn primary" data-act="rate" data-currency="${esc(c)}" data-date="${esc(rateDay())}">Fetch or enter a ${esc(c)} rate</button>`).join('')}</div></div>
            <div class="table-wrap"><table class="t sl-stack"><thead><tr><th>Day</th><th>Pair</th><th class="r">Rate</th><th>Where from</th><th></th></tr></thead><tbody>${rows || '<tr><td colspan="5" class="empty sl-name">No rate saved yet.</td></tr>'}</tbody></table></div>
            ${saved.length > 200 ? `<p class="card-foot">The newest 200 of ${saved.length}.</p>` : ''}</section>`;
    }
    if (tab === 'accounts') {
        const sheet = await sheetFor(currentMonth()).catch(() => null);
        const lineOf = new Map();
        (sheet?.sections || []).forEach(s => s.lines.forEach(l => { if (!l.counted_in) lineOf.set(l.account_id, l); }));
        const restsCell = a => {
            const line = lineOf.get(a.id);
            const enter = a.takes_a_figure ? `<button type="button" class="link sl-enter" data-act="figure" data-account="${a.id}">Enter a figure</button>` : '';
            if (!a.anchor) {
                return a.takes_a_figure ? tag('nofig', 'no figure: enter one', { act: 'figure', data: { account: a.id }, title: 'Enter a figure' })
                    : tag('nofig', 'no statement yet: import one', { href: '#/books/import', title: 'Import a statement' });
            }
            const date = day(a.anchor.date);
            if (a.anchor.source === 'supplied') {
                const age = daysBetween(a.anchor.date, todayIso());
                const stale = age > STALE_DAYS;
                return `your figure ${esc(date)}, <span class="${stale ? 'sl-old' : ''}">${plural(age, 'day')} old</span> ${stale ? tag('stale', 'stale: enter a newer one', { act: 'figure', data: { account: a.id }, title: 'Enter a newer figure' }) : enter}`;
            }
            const check = line && line.check && line.rests_on?.source !== 'supplied' ? checkMarker(line) : '';
            return `statement ${esc(date)} ${check} ${enter}`;
        };
        const rows = r.accounts.map(a => `<tr><td class="sl-name"><a href="#/books/account/${a.id}"><b>${esc(a.name)}</b></a>${accountMark(a.id)}</td><td>${esc(a.type)}</td><td>${esc(a.owner)}</td><td>${esc(a.currency)}</td>
            <td class="small sl-rests">${restsCell(a)}</td>
            <td class="r num sl-fig">${a.anchor ? `<span class="${a.anchor.amount_minor < 0 ? 'neg' : ''}">${esc(money(a.anchor.amount_minor, a.currency))}</span>` : ''}</td>
            <td class="sl-sub">${esc(a.type)} · ${restsCell(a)}</td>
            <td class="r sl-end"><button class="btn sm" data-act="account-edit" data-id="${a.id}">Edit</button></td></tr>`).join('');
        return `${head}<section class="card"><div class="section-header"><h2>Accounts</h2><button class="btn primary" data-act="account-edit" data-id="">${icon('plus')}Add an account</button></div>
            <div class="table-wrap"><table class="t sl-stack sl-accounts"><thead><tr><th>Account</th><th>Kind</th><th>Whose</th><th>Currency</th><th>What its balance rests on</th><th class="r">Latest balance held</th><th></th></tr></thead><tbody>${rows}</tbody></table></div>
            <p class="card-foot">Stale and missing figures say so, and open their fix. Owed balances are negative.</p></section>`;
    }
    if (tab === 'rules') {
        const rules = await get('/api/rules');
        const shown = rules.filter(x => !needle || `${x.pattern} ${x.service_name}`.toLowerCase().includes(needle));
        ACT.__rules = new Map(rules.map(x => [x.id, x]));
        const sets = x => {
            const book = esc(x.book_override || x.book || '');
            const type = x.type_override_id || x.type_name ? esc(x.type_override_id ? r.typeById.get(x.type_override_id)?.display_name || r.typeById.get(x.type_override_id)?.name : x.display_type || x.type_name) : '';
            const range = x.min_amount !== null || x.max_amount !== null
                ? `only ${x.min_amount !== null ? esc(money(toMinor(x.min_amount), 'SGD')) : 'any amount'} to ${x.max_amount !== null ? esc(money(toMinor(x.max_amount), 'SGD')) : 'any amount'}` : '';
            return [book, type, range].filter(Boolean).join(' · ') + (x.book_override || x.type_override_id ? ' ' + tag('yours', 'overrides') : '');
        };
        const rows = shown.slice(0, 200).map(x => `<tr class="clickable" data-act="rule" data-id="${x.id}"><td class="sl-name"><b class="mono-ish">${esc(x.pattern)}</b></td><td class="small">${esc(MATCH_WORDS[x.match_type] || x.match_type)}</td>
            <td>${esc(x.service_name || '')}</td><td class="small">${sets(x)}</td>
            <td class="r num sl-fig">${x.rows_labelled ?? ''}<span class="sl-phone-word"> ${x.rows_labelled === 1 ? 'row' : 'rows'}</span></td>
            <td class="sl-sub">${esc(MATCH_WORDS[x.match_type] || x.match_type)} · gives ${esc(x.service_name || '')}${sets(x) ? ' · ' + sets(x) : ''}</td></tr>`).join('');
        return `${head}<section class="card"><div class="section-header">${search('Search patterns or merchants')}
            <div class="row"><button class="btn" data-act="rerun-rules">${icon('arrows-clockwise')}Re-run every rule</button><button class="btn primary" data-act="rule" data-id="">${icon('plus')}Add a rule</button></div></div>
            <p class="card-foot sl-lead">${shown.length} of ${rules.length} rules${shown.length > 200 ? ' · the first 200 shown' : ''}. A rule gives a row its merchant, and with it a book and type.</p>
            <div class="table-wrap"><table class="t sl-stack"><thead><tr><th>Pattern</th><th>Match</th><th>Merchant</th><th>Sets</th><th class="r">Rows it labelled</th></tr></thead><tbody>${rows || '<tr><td colspan="5" class="empty sl-name">No rule matches.</td></tr>'}</tbody></table></div></section>`;
    }
    // merchants
    const services = await servicesList();
    const filters = { all: () => true, untyped: s => !s.type_id, mixed: s => s.review_each_time, hidden: s => s.exclude_from_expense_views, unused: s => !s.txn_count };
    const shown = services.filter(filters[listState.filter] || filters.all).filter(s => !needle || s.name.toLowerCase().includes(needle));
    ACT.__services = new Map(services.map(s => [s.id, s]));
    const renaming = listState.renaming;
    const nameCell = s => renaming
        ? `<td class="sl-name sl-wide"><input type="text" class="rename-input" data-rename="${s.id}" data-orig="${esc(s.name)}" value="${esc(listState.renames[s.id] ?? s.name)}" aria-label="New name for ${esc(s.name)}"></td>`
        : `<td class="sl-name"><b>${esc(s.name)}</b></td>`;
    const typeCell = s => s.type_id ? esc(s.display_type || s.type_name) : tag('nofig', 'no type', { act: 'merchant', data: { id: s.id }, title: 'Give it a type' });
    const marks = s => `${s.review_each_time ? tag('notchecked', 'mixed') : ''} ${s.exclude_from_expense_views ? tag('yours', 'hidden') : ''} ${s.is_one_off ? tag('yours', 'one-off') : ''}`.trim();
    const rows = shown.slice(0, 200).map(s => `<tr${renaming ? '' : ` class="clickable" data-act="merchant" data-id="${s.id}"`}>${nameCell(s)}<td>${esc(s.book || '')}</td>
        <td>${typeCell(s)}</td><td class="r num${renaming ? '' : ' sl-fig'}">${s.txn_count}<span class="sl-phone-word"> ${s.txn_count === 1 ? 'row' : 'rows'}</span></td><td class="r num">${s.rule_count}</td>
        <td>${marks(s)}</td>
        ${renaming ? '' : `<td class="sl-sub">${esc(s.book || 'no book')} · ${typeCell(s)} · ${plural(s.rule_count, 'rule')} ${marks(s)}</td>`}</tr>`).join('');
    const chip = (k, l, n) => `<button class="chip" data-act="list-filter" data-f="${k}" aria-pressed="${listState.filter === k}">${l} <span class="n">${n}</span></button>`;
    const pending = Object.keys(listState.renames).length;
    return `${head}<section class="card"><div class="section-header">${search('Search merchants')}
            <div class="row">${renaming
                ? `<button class="btn" data-act="rename-mode" data-on="0">Stop renaming</button><button class="btn primary" data-act="rename-save" id="rename-save"${pending ? '' : ' disabled'}>${pending ? `Save ${plural(pending, 'rename')}` : 'Save renames'}</button>`
                : `<button class="btn" data-act="rename-mode" data-on="1">${icon('pencil-simple')}Rename several</button><button class="btn primary" data-act="merchant-add">${icon('plus')}Add a merchant</button>`}</div></div>
        ${renaming ? '<p class="card-foot sl-lead">Type the new names, then save them together: one change in Changes, with Undo. Rules and rows keep pointing at the same merchant.</p>' : ''}
        <div class="chips sl-chips">${chip('all', 'All', services.length)}${chip('untyped', 'No type', services.filter(filters.untyped).length)}${chip('mixed', 'Mixed', services.filter(filters.mixed).length)}${chip('hidden', 'Hidden', services.filter(filters.hidden).length)}${chip('unused', 'No rows', services.filter(filters.unused).length)}</div>
        <div class="table-wrap"><table class="t sl-stack${renaming ? ' sl-renaming' : ''}"><thead><tr><th>Merchant</th><th>Book</th><th>Type</th><th class="r">Rows</th><th class="r">Rules</th><th></th></tr></thead><tbody>${rows || '<tr><td colspan="6" class="empty sl-name">None.</td></tr>'}</tbody></table></div>
        ${shown.length > 200 ? `<p class="card-foot">The first 200 of ${shown.length}; search to narrow.</p>` : ''}</section>`;
}
ACT['list-filter'] = el => { listState.filter = el.dataset.f; rerender(); };
listState.renaming = false;
listState.renames = {};
ACT['rename-mode'] = el => { listState.renaming = el.dataset.on === '1'; listState.renames = {}; rerender(); };
document.addEventListener('input', e => {
    const el = e.target.closest('[data-rename]');
    if (!el) return;
    const name = el.value.trim();
    if (name && name !== el.dataset.orig) listState.renames[el.dataset.rename] = name; else delete listState.renames[el.dataset.rename];
    const n = Object.keys(listState.renames).length;
    const btn = $('#rename-save');
    if (btn) { btn.disabled = !n; btn.textContent = n ? `Save ${plural(n, 'rename')}` : 'Save renames'; }
});
ACT['rename-save'] = async () => {
    const renames = Object.entries(listState.renames).map(([id, name]) => ({ id: Number(id), name }));
    if (!renames.length) return;
    const r = await act('POST', '/api/services/bulk-rename', { renames }, `Renamed ${plural(renames.length, 'merchant')}`);
    if (!r) return;
    if (r.data.errors && r.data.errors.length) toast(`${r.data.updated} renamed; ${r.data.errors.length} could not be: a merchant of that name may exist already`, { bad: true, ms: 9000 });
    listState.renames = {}; listState.renaming = false; rerender();
};
document.addEventListener('keydown', e => { if (e.key === 'Enter' && e.target.id === 'ls-search') { listState.search = e.target.value.trim(); rerender(); } });
document.addEventListener('search', e => { if (e.target.id === 'ls-search') { listState.search = e.target.value.trim(); rerender(); } }, true);

function merchantFields(r, s) {
    return `<label class="field"><span>Name</span><input type="text" id="mc-name" value="${esc(s?.name || '')}"></label>
        <div class="fields two"><label class="field"><span>Book</span><select id="mc-book">${bookOptions(r.books, s?.book, s ? '—' : 'As the type says')}</select></label>
            <label class="field"><span>Type</span><select id="mc-type">${typeOptions(r.types, s?.type_id, { blank: 'No type' })}</select></label></div>
        <label class="field"><span>Note</span><textarea id="mc-notes" rows="2" placeholder="Anything worth remembering about it">${esc(s?.notes || '')}</textarea></label>
        <label class="check"><input type="checkbox" id="mc-mixed"${s?.review_each_time ? ' checked' : ''}> Mixed merchant: look at its rows each time</label>
        <label class="check"><input type="checkbox" id="mc-hidden"${s?.exclude_from_expense_views ? ' checked' : ''}> Hide from spending lists (never from a sum)</label>`;
}
ACT['merchant-add'] = async () => {
    const r = await refs();
    openSheet('Add a merchant', `<div class="fields">${merchantFields(r, null)}</div>
        <p class="small muted">A merchant takes rows through its rules: add a rule for it next, from Rules.</p>`,
    { eyebrow: 'Merchants', foot: '<button class="btn primary" data-act="merchant-create">Add the merchant</button>' });
};
ACT['merchant-create'] = async () => {
    const name = $('#mc-name').value.trim();
    if (!name) { toast('Give it a name', { bad: true }); return; }
    const body = { name, book: $('#mc-book').value || null, type_id: $('#mc-type').value ? Number($('#mc-type').value) : null, notes: $('#mc-notes').value.trim() || null,
        review_each_time: $('#mc-mixed').checked ? 1 : 0, exclude_from_expense_views: $('#mc-hidden').checked ? 1 : 0 };
    const r = await act('POST', '/api/services', body, 'Merchant added');
    if (r) { closeSheet(); rerender(); }
};
ACT.merchant = async el => {
    const r = await refs();
    const s = ACT.__services.get(Number(el.dataset.id));
    const others = [...ACT.__services.values()].filter(x => x.id !== s.id);
    openSheet(esc(s.name), `<div class="fields">${merchantFields(r, s)}
        <label class="check"><input type="checkbox" id="mc-oneoff"${s.is_one_off ? ' checked' : ''}> One-off</label>
        <p class="small muted">${plural(s.txn_count, 'row')} · ${plural(s.rule_count, 'rule')}${(s.rules || []).length ? ': ' + s.rules.map(x => esc(x.pattern)).join(', ') : ''}. A change of book or type is written to the rows that take their label from it.</p></div>
        <button class="btn primary block" data-act="merchant-save" data-id="${s.id}">Save</button>
        <hr class="rule"><h3>Clean up</h3>
        <div class="sl-merge"><label class="field"><span>Merge into</span><select id="mc-target"><option value="">Choose a merchant</option>${others.map(x => `<option value="${x.id}">${esc(x.name)}</option>`).join('')}</select></label>
            <button class="btn" data-act="merchant-merge" data-id="${s.id}">Merge</button></div>
        <button class="btn danger" data-act="merchant-delete" data-id="${s.id}">${icon('trash')}Delete this merchant</button>`, { eyebrow: 'Merchant' });
};
ACT['merchant-save'] = async el => {
    const body = { name: $('#mc-name').value.trim(), book: $('#mc-book').value || null, type_id: $('#mc-type').value ? Number($('#mc-type').value) : null,
        notes: $('#mc-notes').value.trim() || null,
        review_each_time: $('#mc-mixed').checked ? 1 : 0, exclude_from_expense_views: $('#mc-hidden').checked ? 1 : 0, is_one_off: $('#mc-oneoff').checked ? 1 : 0 };
    const r = await act('PUT', `/api/services/${el.dataset.id}`, body, 'Merchant saved');
    if (r) { closeSheet(); rerender(); }
};
ACT['merchant-merge'] = async el => {
    const target = $('#mc-target').value;
    if (!target) { toast('Choose the merchant to merge into', { bad: true }); return; }
    const r = await act('POST', `/api/services/${el.dataset.id}/merge`, { target_id: Number(target) }, 'Merged');
    if (r) { closeSheet(); rerender(); }
};
ACT['merchant-delete'] = async el => {
    if (!confirm('Delete this merchant? You can undo it from Changes.')) return;
    const r = await act('DELETE', `/api/services/${el.dataset.id}`, undefined, 'Merchant deleted');
    if (r) { closeSheet(); rerender(); }
};

ACT.rule = async el => {
    const [r, services] = await Promise.all([refs(), servicesList()]);
    const x = el.dataset.id ? ACT.__rules.get(Number(el.dataset.id)) : null;
    openSheet(x ? esc(x.pattern) : 'Add a rule', `<div class="fields">
        <div class="fields two"><label class="field"><span>Pattern</span><input type="text" id="rl-pattern" value="${esc(x?.pattern || '')}"></label>
            <label class="field"><span>Match</span><select id="rl-match">${['contains', 'startswith', 'exact'].map(m => `<option value="${m}"${(x?.match_type || 'contains') === m ? ' selected' : ''}>${MATCH_WORDS[m]}</option>`).join('')}</select></label></div>
        <label class="field"><span>Merchant</span><select id="rl-svc"><option value="">Choose</option>${services.map(s => `<option value="${s.id}"${s.id === x?.service_id ? ' selected' : ''}>${esc(s.name)}</option>`).join('')}</select></label>
        <div class="fields two"><label class="field"><span>Book, in place of the merchant’s</span><select id="rl-book">${bookOptions(r.books, x?.book_override, 'The merchant’s')}</select></label>
            <label class="field"><span>Type, in place of the merchant’s</span><select id="rl-type">${typeOptions(r.types, x?.type_override_id, { blank: 'The merchant’s' })}</select></label></div>
        <div class="fields two"><label class="field"><span>Only from (S$)</span><input type="text" inputmode="decimal" id="rl-min" value="${x?.min_amount ?? ''}"></label>
            <label class="field"><span>Only up to (S$)</span><input type="text" inputmode="decimal" id="rl-max" value="${x?.max_amount ?? ''}"></label></div></div>
        ${x ? `<p class="small muted">It labels ${plural(x.rows_labelled ?? 0, 'row')} now.</p>` : ''}
        <p class="small muted">Rows already labelled by hand keep their labels. Re-run every rule to apply a change to the rows the rules labelled.</p>`,
    { eyebrow: x ? 'Rule' : 'Rules', foot: `${x ? `<button class="btn danger" data-act="rule-delete" data-id="${x.id}">${icon('trash')}Delete</button>` : ''}<button class="btn primary" data-act="rule-save" data-id="${x ? x.id : ''}">Save</button>` });
};
ACT['rule-save'] = async el => {
    const v = id => $('#' + id).value.trim();
    if (!v('rl-svc')) { toast('Choose the merchant', { bad: true }); return; }
    const body = { pattern: v('rl-pattern'), match_type: v('rl-match'), service_id: Number(v('rl-svc')), book_override: v('rl-book') || null,
        type_override_id: v('rl-type') ? Number(v('rl-type')) : null, min_amount: v('rl-min') || null, max_amount: v('rl-max') || null };
    const r = el.dataset.id ? await act('PUT', `/api/rules/${el.dataset.id}`, body, 'Rule saved') : await act('POST', '/api/rules', body, 'Rule added');
    if (r) { closeSheet(); rerender(); }
};
ACT['rule-delete'] = async el => {
    if (!confirm('Delete this rule? You can undo it from Changes.')) return;
    const r = await act('DELETE', `/api/rules/${el.dataset.id}`, undefined, 'Rule deleted');
    if (r) { closeSheet(); rerender(); }
};
ACT['rerun-rules'] = async () => {
    if (!confirm('Re-run every rule on the rows the rules labelled? Rows you labelled by hand are left alone. It is one change in Changes, with Undo.')) return;
    const r = await act('POST', '/api/rules/recategorize', {}, 'Rules re-run');
    if (r) rerender();
};

ACT['account-edit'] = async el => {
    const r = await refs();
    const a = el.dataset.id ? r.accountById.get(Number(el.dataset.id)) : null;
    // P8.4: the choice shows the short word; what it means sits under it.
    const opt = (list, v) => list.map(x => `<option value="${esc(x.name)}"${x.name === v ? ' selected' : ''}>${esc(x.name)}</option>`).join('');
    const meaning = (list, v) => esc(list.find(x => x.name === v)?.description || '');
    openSheet(a ? esc(a.name) : 'Add an account', `<div class="fields">
        <label class="field"><span>Name</span><input type="text" id="ac-name" value="${esc(a?.name || '')}"></label>
        ${a ? '' : `<label class="field"><span>Kind</span><select id="ac-kind" data-meaning="ac-kind-hint">${opt(r.kinds.kinds, 'bank')}</select><span class="hint" id="ac-kind-hint">${meaning(r.kinds.kinds, 'bank')}</span></label>
        <label class="field"><span>Whose</span><select id="ac-owner" data-meaning="ac-owner-hint">${opt(r.kinds.owners, 'Household')}</select><span class="hint" id="ac-owner-hint">${meaning(r.kinds.owners, 'Household')}</span></label>
        <label class="field"><span>Currency</span><select id="ac-cur">${['SGD', 'INR', 'USD'].map(c => `<option>${c}</option>`).join('')}</select></label>`}
        <label class="field"><span>Last four digits</span><input type="text" inputmode="numeric" maxlength="4" id="ac-last4" value="${esc(a?.last_four || '')}"></label>
        ${a ? `<label class="check"><input type="checkbox" id="ac-archived"${a.status === 'archived' ? ' checked' : ''}> Archived: shown apart, still counted</label>` : ''}</div>
        ${a && a.takes_a_figure ? `<button class="btn block" data-act="figure" data-account="${a.id}">Enter a figure</button>` : ''}`,
    { eyebrow: a ? 'Account' : 'Accounts', foot: `<button class="btn primary" data-act="account-save" data-id="${a ? a.id : ''}">Save</button>` });
    $$('#sheets [data-meaning]').forEach(sel => sel.addEventListener('change', () => {
        const list = sel.id === 'ac-kind' ? r.kinds.kinds : r.kinds.owners;
        $('#' + sel.dataset.meaning).textContent = list.find(x => x.name === sel.value)?.description || '';
    }));
};
ACT['account-save'] = async el => {
    const name = $('#ac-name').value.trim();
    if (!name) { toast('Give it a name', { bad: true }); return; }
    let r;
    if (el.dataset.id) {
        r = await act('PUT', `/api/accounts/${el.dataset.id}`, { name, last_four: $('#ac-last4').value.trim() || null, status: $('#ac-archived').checked ? 'archived' : 'active' }, 'Account saved');
    } else {
        r = await act('POST', '/api/accounts', { name, type: $('#ac-kind').value, owner: $('#ac-owner').value, currency: $('#ac-cur').value, last_four: $('#ac-last4').value.trim() || null }, 'Account added');
    }
    if (r) { closeSheet(); rerender(); }
};

// ---------------------------------------------------------------------------
// Import: three results, each drawn as a sum; a drop strip on the desk
// ---------------------------------------------------------------------------

const importState = { preview: null, done: null, busy: false, showEmpty: false };
async function viewImport() {
    const [past, coverage] = await Promise.all([get('/api/import/history').catch(() => []), get('/api/statements/coverage?months=6').catch(() => null)]);
    const p = importState.preview;
    afterRender(wireDrop);
    return `${booksNav('import')}
    <div class="page-head"><div><span class="eyebrow">Books · Import</span><h1>Import</h1><p>Statements go through one tie check: opening balance and the rows must come to the closing balance, exactly. One that does not tie is refused whole.</p></div></div>
    <section class="card import-drop">
        <div class="drop desk-only" id="drop"><span class="drop__icon">${icon('upload-simple')}</span><b>Drop statement files here</b>
            <span>or <label class="link" for="imp-files">choose them</label>. Each goes through the same tie check.</span></div>
        <div class="phone-only"><label class="btn primary block" for="imp-files">${icon('upload-simple')}Choose statement files</label></div>
        <input type="file" id="imp-files" multiple hidden data-change="import-files">
        ${importState.busy ? '<p class="loading">Reading…</p>' : ''}
    </section>
    ${p ? importPreviewHTML(p) : ''}
    ${importState.done ? `<div class="notice ok import-done">${icon('check-circle')}<span>${esc(importState.done)}</span></div>` : ''}
    ${coverage ? coverageHTML(coverage) : ''}
    ${pastImportsHTML(past)}`;
}
ACT['choose-files'] = () => $('#imp-files')?.click();

/** What one past import did, in plain words. */
function pastState(b) {
    if (b.status === 'committed') return `<span class="tag done">${icon('check-circle')}imported</span>`;
    if (b.status === 'failed') return tag('off', 'did not import');
    const st = b.statements || [];
    if (st.length && st.every(s => s.status === 'off')) return tag('refused', 'refused');
    return tag('notchecked', 'looked at, not imported');
}
/** Each statement's marker on a past import, linked to its account's tie line. */
function pastResult(b) {
    const st = b.statements;
    if (!st) return '<span class="muted small">not kept</span>';
    if (!st.length) return '<span class="muted small">no statement</span>';
    const several = new Set(st.map(s => s.account)).size > 1;
    return `<div class="past-result">${st.map(s => {
        const who = several ? `${s.account.replace(/ \S*\d{4}$/, '')} ` : '';
        const date = s.date ? ` ${day(s.date, { year: false })}` : '';
        const text = s.status === 'ties' ? `${who}ties${date}` : s.status === 'off' ? `${who}off by ${money(Math.abs(s.difference_minor || 0), s.currency || 'SGD')}${date}` : `${who}not checked`;
        const kind = s.status === 'ties' ? 'ties' : s.status === 'off' ? 'off' : 'notchecked';
        return tag(kind, text, s.account_id ? { href: `#/books/account/${s.account_id}`, title: 'See the tie line' } : {});
    }).join('')}</div>`;
}
function pastImportsHTML(past) {
    const empty = past.filter(b => !b.total_lines && !(b.accounts || []).length && !(b.statements || []).length);
    const listed = importState.showEmpty ? past : past.filter(b => !empty.includes(b));
    const rows = listed.map(b => `<tr><td data-label="When">${esc(when(b.created_at))}</td><td data-label="Accounts">${b.accounts.length ? b.accounts.map(esc).join(', ') : '<span class="muted">none read</span>'}</td>
        <td class="r num" data-label="Rows">${b.total_lines}</td><td data-label="What happened">${pastState(b)}</td><td data-label="Result">${pastResult(b)}</td></tr>`).join('');
    return `<section class="card"><div class="section-header"><h2>Past imports</h2><span class="section-header__aside">${plural(past.length - empty.length, 'import')}</span></div>
        <div class="table-shell"><table class="t cells past-table"><thead><tr><th>When</th><th>Accounts</th><th class="r">Rows</th><th>What happened</th><th>Result</th></tr></thead><tbody>
        ${rows || '<tr><td colspan="5" class="empty">None yet.</td></tr>'}</tbody></table></div>
        ${empty.length ? `<p class="card-foot">${importState.showEmpty ? `${plural(empty.length, 'empty look')} with no file read ${empty.length === 1 ? 'is' : 'are'} shown.` : `${plural(empty.length, 'empty look')} with no file read ${empty.length === 1 ? 'is' : 'are'} hidden.`}
            <button class="link" data-act="past-empty">${importState.showEmpty ? 'Hide them' : 'Show them'}</button></p>` : ''}</section>`;
}
ACT['past-empty'] = () => { importState.showEmpty = !importState.showEmpty; rerender(); };

/** Which statements are held: each bank and card account against the last six
 *  months, the last closed month marked as the one to have. */
function coverageHTML(c) {
    const { target_month: target, covered, total } = c.summary;
    const missing = total - covered;
    const label = m => monthName(m, { short: true }).replace(/ (\d{2})(\d{2})$/, ' ’$2');
    const old = i => i < c.months.length - 3 ? ' cov-old' : '';
    const head = c.months.map((m, i) => `<th class="${m === target ? 'target' : ''}${old(i)}">${esc(label(m))}</th>`).join('');
    const rows = c.accounts.map(a => `<tr><td><a href="#/books/account/${a.id}">${esc(window.__accountById?.get(a.id)?.name || a.short_name)}</a> <span class="small muted">${a.type === 'bank' ? 'bank' : 'card'}</span></td>${c.months.map((m, i) => {
        const cell = c.matrix[a.id]?.[m];
        const cls = `${m === target ? ' target' : ''}${old(i)}`;
        if (cell && cell.imported) return `<td class="cov-ok${cls}" title="statement held${cell.date ? ', imported ' + esc(day(cell.date)) : ''}">${icon('check-circle')}<span class="sr-only">held</span></td>`;
        if (m === target) return `<td class="cov-missing${cls}">${tag('nofig', 'missing', { act: 'choose-files', title: `No statement for ${monthName(m)}: import one` })}</td>`;
        return `<td class="cov-none${cls}" title="no statement for ${esc(monthName(m))}">${icon('circle-dashed')}<span class="sr-only">none</span></td>`;
    }).join('')}</tr>`).join('');
    return `<section class="card"><div class="section-header"><div><h2>Statements held</h2>
        <p>${missing ? `${covered} of ${total} accounts have a statement for ${esc(monthName(target))}: <b class="warn-word">${missing} missing</b>` : `All ${total} accounts have a statement for ${esc(monthName(target))}.`}</p></div></div>
        <div class="table-shell"><table class="t coverage"><thead><tr><th>Account</th>${head}</tr></thead><tbody>${rows || `<tr><td colspan="${c.months.length + 1}" class="empty">No bank or card account yet.</td></tr>`}</tbody></table></div></section>`;
}
function wireDrop() {
    const zone = $('#drop');
    if (!zone) return;
    zone.addEventListener('dragover', e => { e.preventDefault(); zone.classList.add('over'); });
    zone.addEventListener('dragleave', () => zone.classList.remove('over'));
    zone.addEventListener('drop', e => { e.preventDefault(); zone.classList.remove('over'); uploadFiles(e.dataTransfer.files); });
}
ACT['import-files'] = el => uploadFiles(el.files);
async function uploadFiles(files) {
    if (!files || !files.length) return;
    const form = new FormData();
    Array.from(files).forEach(f => form.append('files', f));
    importState.busy = true; importState.done = null; rerender();
    const r = await send('POST', '/api/import/upload', form);
    importState.busy = false;
    if (!r.ok) { toast(r.data.error || 'fin could not read those files', { bad: true }); rerender(); return; }
    importState.preview = r.data;
    rerender();
}
/** The same six lines for every statement: opening, money out, money in,
 *  what the rows make it, the closing it states, and the result. */
function stmtSum(s, cur) {
    const known = s.opening !== null && s.opening !== undefined;
    const m = v => esc(money(v, cur));
    const none = '<span class="muted">none in the file</span>';
    const rowsWord = n => n === null || n === undefined ? '' : n ? `, ${plural(n, 'row')}` : ', none';
    const result = s.status === 'ties'
        ? `<tr class="gap zero"><td class="op">${icon('check-circle')}</td><td>difference</td><td class="r">${m(0)}</td></tr>`
        : s.status === 'off'
            ? `<tr class="gap"><td class="op">≠</td><td>off by</td><td class="r">${m(Math.abs(s.difference))}</td></tr>`
            : `<tr class="gap unchecked"><td class="op">${icon('circle-dashed')}</td><td>not checked</td><td class="r">no balance</td></tr>`;
    return `<table class="sum small"><tbody>
        <tr><td class="op"></td><td>opening${s.opening_date ? ' ' + esc(day(s.opening_date, { year: false })) : ''}</td><td class="r">${known ? m(s.opening) : none}</td></tr>
        <tr><td class="op">−</td><td>money out${rowsWord(s.outCount)}</td><td class="r">${m(s.out)}</td></tr>
        <tr><td class="op">+</td><td>money in${rowsWord(s.inCount)}</td><td class="r">${m(s.in)}</td></tr>
        <tr class="eq"><td class="op">=</td><td>what the rows make it</td><td class="r">${known ? m(s.opening + s.in - s.out) : '<span class="muted">cannot say</span>'}</td></tr>
        <tr><td class="op"></td><td>closing it states</td><td class="r">${known ? m(s.closing) : none}</td></tr>
        ${result}</tbody></table>`;
}
function importPreviewHTML(p) {
    const ties = p.groups.filter(g => g.tie === 'ties');
    const unchecked = p.groups.filter(g => g.tie !== 'ties' && g.total);
    const refused = p.errors.filter(e => e.tie);
    const unread = p.errors.filter(e => !e.tie);
    const rows = p.groups.reduce((a, g) => a + g.transactions.length, 0);
    const count = (list, test) => list.filter(test).length;
    const card = (kind, pill, n, body) => `<section class="card result result--${kind}"><div class="result__head">${pill}<span class="result__n">${n}</span></div>${body}</section>`;
    const stmtBlock = (account, sub, sum) => `<div class="result-stmt"><div class="result-stmt__name">${esc(account)}</div><div class="small muted">${sub}</div>${sum}</div>`;
    const noneYet = '<p class="small muted result-none">None.</p>';
    const tiesBody = ties.flatMap(g => g.statements.map((t, i) => {
        const mine = g.transactions.filter(x => x.statement === i);
        return stmtBlock(g.account, `statement ${esc(day(t.closing_date))}`, stmtSum({
            status: 'ties', opening: t.opening_minor, opening_date: t.opening_date, closing: t.closing_minor,
            in: t.in_minor ?? 0, out: t.out_minor ?? 0, inCount: count(mine, x => x.amount_sgd < 0), outCount: count(mine, x => x.amount_sgd > 0),
        }, g.currency));
    })).join('') || noneYet;
    const refusedBody = refused.map(e => stmtBlock(e.tie.account, `statement ${esc(day(e.tie.closing_date))}: none of its rows will be imported`, stmtSum({
        status: 'off', opening: e.tie.opening_minor, opening_date: e.tie.opening_date, closing: e.tie.closing_minor, difference: e.tie.difference_minor,
        in: e.tie.in_minor ?? 0, out: e.tie.out_minor ?? 0,
    }, e.tie.currency))).join('') || noneYet;
    const uncheckedBody = unchecked.map(g => {
        const out = g.transactions.filter(t => t.amount_sgd > 0), inn = g.transactions.filter(t => t.amount_sgd < 0);
        return stmtBlock(g.account, `it states no balance: ${plural(g.total, 'row')} come in unchecked`, stmtSum({
            status: 'not_checked', opening: null,
            out: out.reduce((a, t) => a + toMinor(t.amount_sgd, g.currency), 0), outCount: out.length,
            in: inn.reduce((a, t) => a - toMinor(t.amount_sgd, g.currency), 0), inCount: inn.length,
        }, g.currency));
    }).join('') || noneYet;
    const groupsHTML = p.groups.filter(g => g.transactions.length).map(g => `<details class="card import-group"><summary><b>${esc(g.account)}</b> · ${plural(g.transactions.length, 'row')} · ${g.typed} with a type, ${g.untyped} without</summary>
        <div class="table-shell" style="margin-top:10px"><table class="t rows acct-rows"><thead><tr><th>Date</th><th>Description</th><th>Labelled</th><th class="r">Money in</th><th class="r">Money out</th></tr></thead><tbody>
        ${g.transactions.map(t => {
            const minor = toMinor(t.amount_sgd, g.currency);
            return `<tr><td class="num row-date">${esc(day(t.date, { year: false }))}</td><td class="row-desc">${esc(t.description)}</td><td class="row-label">${t.type_name ? esc(t.type_name) : t.flow_type === 'review' ? tag('nofig', 'will wait for a label') : t.flow_type !== 'expense' ? `<span class="muted">${esc(t.flow_type)}</span>` : tag('nofig', 'no type')}</td>
            <td class="r num row-in">${minor < 0 ? esc(money(-minor, g.currency)) : ''}</td><td class="r num row-out">${minor >= 0 ? esc(money(minor, g.currency)) : ''}</td></tr>`;
        }).join('')}</tbody></table></div></details>`).join('');
    return `<div class="results">${card('ties', tag('ties', 'Ties'), ties.reduce((a, g) => a + g.statements.length, 0), tiesBody)}${card('off', tag('off', 'Off by'), refused.length, refusedBody)}${card('notchecked', tag('notchecked', 'Not checked'), unchecked.length, uncheckedBody)}</div>
        ${unread.length ? `<div class="notice bad import-unread">${icon('warning-circle')}<span>${unread.map(e => `${esc(e.file)}: ${esc(e.error)}`).join('<br>')}</span></div>` : ''}
        ${rows ? `<section class="card import-bar"><p>${plural(rows, 'row')} ready. A refused statement stays in the queue until a file that ties is imported.</p>
            <div class="row"><button class="btn" data-act="import-clear">Start again</button><button class="btn primary" data-act="import-confirm">Import ${plural(rows, 'row')}</button></div></section>${groupsHTML}`
            : `<section class="card import-bar"><p>Nothing to import.</p><div class="row"><button class="btn" data-act="import-clear">Start again</button></div></section>`}`;
}
ACT['import-clear'] = () => { importState.preview = null; importState.done = null; rerender(); };
ACT['import-confirm'] = async el => {
    const p = importState.preview;
    el.disabled = true;
    const r = await send('POST', '/api/import/confirm', {
        import_id: p.import_id,
        groups: p.groups.map(g => ({ account: g.account, transactions: g.transactions, statements: g.statements || [] })),
    });
    if (!r.ok) { el.disabled = false; toast(r.data.error || 'Nothing was imported', { bad: true }); return; }
    importState.preview = null;
    importState.done = `Imported ${plural(r.data.transactions_saved || 0, 'row')}. It is one change in Changes, with Undo.`;
    toast('Imported', { undo: r.change });
    rerender();
};

// ---------------------------------------------------------------------------
// Changes: every write, yours and Claude's, one undoable list
// ---------------------------------------------------------------------------

// Who made a change and where, in plain words (walk C3.2): never the
// gate's client name, and the change number only inside an opened change.
function whereFrom(e) {
    if (e.via !== 'chat') return 'in fin';
    const a = String(e.actor || '').toLowerCase();
    if (/phone|mobile|ios|android/.test(a)) return 'on phone';
    if (a.includes('code')) return 'in Claude Code';
    if (a.includes('desktop')) return 'in the Claude app';
    if (a.includes('web')) return 'on the web';
    return 'in chat';
}
function whoTag(e) {
    return e.via === 'chat' ? tag('claude', 'Claude') : tag('you', 'you');
}
function whoName(x, { cap = true } = {}) { return x && x.via === 'chat' ? 'Claude' : (cap ? 'You' : 'you'); }
function whoseName(x) { return x && x.via === 'chat' ? 'Claude’s' : 'your'; }
/** A time inside a sentence: "at 20:53" today, else "on 3 Oct, 20:53". */
function timeWords(utc) {
    const w = when(utc);
    return w.startsWith('today ') ? `at ${w.slice(6)}` : `on ${w}`;
}
/** The viewer's own day of a history time, for the day headings (C3.3). */
function localDay(utc) {
    const t = new Date(String(utc).replace(' ', 'T') + 'Z');
    if (Number.isNaN(t.getTime())) return '';
    return `${t.getFullYear()}-${String(t.getMonth() + 1).padStart(2, '0')}-${String(t.getDate()).padStart(2, '0')}`;
}
function dayHeading(iso) {
    const today = todayIso();
    const y = new Date(); y.setDate(y.getDate() - 1);
    const yesterday = `${y.getFullYear()}-${String(y.getMonth() + 1).padStart(2, '0')}-${String(y.getDate()).padStart(2, '0')}`;
    if (iso === today) return 'Today';
    if (iso === yesterday) return 'Yesterday';
    return iso ? day(iso) : 'Earlier';
}

const SKIP_FIELDS = new Set(['id', 'created_at', 'cat_source', 'flow_type_manual', 'imported_at', 'fetched_at', 'updated_at', 'statement_id', 'printed']);
const FIELD_WORDS = { type_id: 'type', service_id: 'merchant', other_side_id: 'other side', flow_type: 'kind', is_one_off: 'one-off', notes: 'note',
    amount_minor: 'amount', account_id: 'account', type_override_id: 'type (rule)', book_override: 'book (rule)', review_each_time: 'mixed',
    exclude_from_expense_views: 'hidden', match_type: 'match', min_amount_minor: 'from', max_amount_minor: 'up to', amount: 'amount', statement_date: 'statement' };
// An empty old or new value said plainly, never as bad news (C4.2).
const EMPTY_WORDS = { type_id: 'no type', type_override_id: 'no type', service_id: 'no merchant', other_side_id: 'none', notes: 'empty' };
const FLOW_WORDS = { expense: 'money out', income: 'money in', refund: 'a refund', review: 'waiting for a label', transfer: 'a move between own accounts' };
function isEmpty(v) { return v === null || v === undefined || v === ''; }
function changedFields(c) {
    const b = c.before || {}, a = c.after || {};
    return Object.keys(a).filter(k => !SKIP_FIELDS.has(k) && JSON.stringify(b[k]) !== JSON.stringify(a[k]));
}
/** A stored value as words (escaped), or '' when empty. */
function valueText(table, field, value, ctx, cur = 'SGD') {
    if (isEmpty(value)) return '';
    if (field === 'type_id' || field === 'type_override_id') return esc(ctx.r.typeById.get(value)?.display_name || 'a type no longer listed');
    if (field === 'other_side_id' || field === 'account_id') return esc(ctx.r.accountById.get(value)?.name || 'an account no longer listed');
    if (field === 'service_id') return esc(ctx.services.find(s => s.id === value)?.name || 'a merchant no longer listed');
    if (['is_one_off', 'review_each_time', 'exclude_from_expense_views'].includes(field)) return value ? 'yes' : 'no';
    if (field === 'flow_type') return esc(FLOW_WORDS[value] || String(value).replace(/_/g, ' '));
    // Each changed row comes with its own currency (its account's): rupees
    // keep Indian grouping here as everywhere.
    if (table === 'transactions' && field === 'amount_minor') return esc(money(-value, cur, { signed: true }));
    if (table === 'anchors' && field === 'amount') return esc(money(value, cur));
    if (field === 'amount_minor' || field.endsWith('_amount_minor')) return esc(money(value, cur));
    if (/date$/.test(field) && typeof value === 'string') return esc(day(value));
    return esc(String(value));
}
function emptyText(field) { return EMPTY_WORDS[field] || 'empty'; }
function rowName(c) {
    const row = c.after || c.before || {};
    return row.description || row.name || row.pattern || row.pair || '';
}
function accountOf(c, ctx) {
    const row = c.after || c.before || {};
    const id = c.account_id ?? row.account_id;
    return id ? (ctx.r.accountById.get(id)?.name || '') : '';
}
/** A bank row's amount as a change shows it: money out negative (S$ −84.20), money in with a plus. */
function txAmount(c) {
    const row = c.after || c.before || {};
    return isEmpty(row.amount_minor) ? '' : money(-row.amount_minor, c.currency || 'SGD', { signed: true });
}

/** What one change to a bank row did, as clauses: the first names the row
 *  (subject), the rest say "it". Lower case; the caller capitalises. */
function rowClauses(c, ctx, subject) {
    const b = c.before || {}, a = c.after || {};
    const out = [];
    for (const k of changedFields(c)) {
        const s = out.length ? 'it' : subject;
        const now = valueText(c.table, k, a[k], ctx, c.currency), was = valueText(c.table, k, b[k], ctx, c.currency);
        if (k === 'type_id') out.push(now ? `typed ${s} as ${now}` : `took the type off ${s}`);
        else if (k === 'notes') out.push(!now ? `removed the note “${was}” on ${s}` : !was ? `noted “${now}” on ${s}` : `changed the note on ${s} to “${now}”`);
        else if (k === 'is_one_off') out.push(a[k] ? `marked ${s} one-off` : `marked ${s} as not one-off`);
        else if (k === 'service_id') out.push(now ? `gave ${s} the merchant ${now}` : `took the merchant off ${s}`);
        else if (k === 'book') out.push(now ? `moved ${s} to the ${now} book` : `took the book off ${s}`);
        else if (k === 'flow_type') out.push(`labelled ${s} as ${now}`);
        else if (k === 'other_side_id') out.push(now ? `linked ${s} to ${now}` : `took the other side off ${s}`);
        else if (k === 'description') out.push(`renamed ${s} to “${now}”`);
        else if (k === 'date') out.push(`moved ${s} to ${now}`);
        else out.push(`changed the ${esc(FIELD_WORDS[k] || k.replace(/_/g, ' '))} of ${s}`);
    }
    return out;
}
function joinClauses(list, max = 3) {
    const shown = list.slice(0, max);
    if (list.length > max) shown.push('more');
    if (shown.length <= 1) return shown[0] || '';
    return `${shown.slice(0, -1).join(', ')} and ${shown[shown.length - 1]}`;
}
/** What a change did, in words, from its first row (walk C3.1): "typed GIANT
 *  HYPERMARKET as Groceries and noted “Party supplies”". subject overrides the
 *  row's name ("it" in a sentence about the row). '' when words fail. */
function changeWords(first, ctx, { rows = 1, subject = null } = {}) {
    if (!first) return '';
    const c = first, a = c.after || {}, b = c.before || {};
    const name = esc(rowName(c));
    const acct = esc(accountOf(c, ctx) || 'an account');
    if (c.table === 'statements' && c.op === 'insert') return `imported the ${acct} statement of ${esc(day(a.statement_date))}`;
    if (c.table === 'statements' && c.op === 'delete') return `took out the ${acct} statement of ${esc(day(b.statement_date))}`;
    const many = rows > 1;
    if (many && !first.same_change) return '';
    if (c.table === 'transactions') {
        const subj = subject || (many ? (first.same_label ? `${rows} ${name} rows` : `${rows} rows`) : name);
        if (c.op === 'insert') return `added ${subj}`;
        if (c.op === 'delete') return `took out ${subj}`;
        return joinClauses(rowClauses(c, ctx, subj));
    }
    if (many) return '';
    if (c.table === 'anchors') {
        const fig = valueText('anchors', 'amount', (c.op === 'delete' ? b : a).amount, ctx, c.currency);
        if (c.op === 'insert') return `entered a figure for ${acct}: ${fig}`;
        if (c.op === 'delete') return `deleted the figure for ${acct} of ${esc(day(b.date))}`;
        return `corrected the figure for ${acct} to ${fig}`;
    }
    const noun = { services: 'the merchant', merchant_rules: 'the rule', accounts: 'the account', subscriptions: 'the bill', rates: 'the rate' }[c.table];
    if (!noun) return '';
    const what = c.table === 'merchant_rules' ? `${noun} “${name}”` : c.table === 'rates' ? `${noun} ${name} for ${esc(day((a.date || b.date || '')))}` : `${noun} ${name}`;
    if (c.op === 'insert') return `added ${what}`;
    if (c.op === 'delete') return `deleted ${what}`;
    if (c.table !== 'rates' && b.name && a.name && b.name !== a.name) return `renamed ${noun} ${esc(b.name)} to ${esc(a.name)}`;
    return `changed ${what}`;
}
/** An undo, said in words (walk C9.1): "Undid Claude’s note on STARBUCKS
 *  SAMPLE: back to “Meeting”". first is the undo's own first row, so its
 *  after is what was put back. */
function undoWords(e, ctx, byId) {
    const whose = whoseName(e.undoes_who);
    const undone = byId.get(e.undoes);
    const c = e.first;
    if (undone && undone.undoes) return `Undid ${whose} undo${c && c.table === 'transactions' && e.rows === 1 ? ` on ${esc(rowName(c))}` : ''}`;
    if (c && c.table === 'transactions' && e.rows === 1 && c.op === 'update') {
        const fields = changedFields(c);
        if (fields.length === 1) {
            const k = fields[0];
            const back = valueText(c.table, k, c.after[k], ctx, c.currency);
            const word = esc(FIELD_WORDS[k] || k.replace(/_/g, ' '));
            return `Undid ${whose} ${word} on ${esc(rowName(c))}: ${back ? `back to “${back}”` : `now ${emptyText(k)}`}`;
        }
        return `Undid ${whose} change to ${esc(rowName(c))}`;
    }
    const plain = String(e.summary || '').replace(/^Undid:\s*/, '');
    return `Undid ${whose} change: ${esc(plain.charAt(0).toLowerCase() + plain.slice(1))}`;
}
function entryTitle(e, ctx, byId) {
    if (e.undoes) return undoWords(e, ctx, byId);
    return sentence(changeWords(e.first, ctx, { rows: e.rows })) || esc(e.summary || 'A change');
}
/** Why an undo is refused, naming who and what (walk C6.2). */
function refusalWords(e, ctx) {
    const by = e.blocked_by;
    const did = changeWords(by.first, ctx, { rows: 1, subject: 'it' });
    const yours = by.via === 'chat' ? 'Claude’s' : 'yours';
    const target = e.rows === 1 ? 'this row' : 'the same rows';
    const what = did && by.first && by.first.table === 'transactions' && e.rows === 1
        ? `${whoName(by, { cap: false })} ${did} ${timeWords(by.at)}`
        : `${esc(sentence(String(by.summary || 'a change')).toLowerCase())} ${timeWords(by.at)}`;
    return `${whoName(by)} changed ${target} after it: ${what}. Undo ${yours} first.`;
}
function entryState(e) {
    if (e.undone_by) return `<span class="state-word">undone</span>`;
    if (e.blocked_by) return tag('refused', 'Undo refused');
    return `<button class="btn sm" data-act="undo" data-entry="${e.id}">${e.undoes ? 'Undo the undo' : 'Undo'}</button>`;
}
function entryMatches(e, mark) {
    const f = S.changes;
    if (f.who === 'claude' && e.via !== 'chat') return false;
    if (f.who === 'you' && e.via === 'chat') return false;
    if (f.state === 'can' && (e.undone_by || e.blocked_by)) return false;
    if (f.state === 'undone' && !e.undone_by) return false;
    if (f.state === 'refused' && !e.blocked_by) return false;
    if (f.state === 'undos' && !e.undoes) return false;
    if (f.newOnly && !(e.id > (mark || 0))) return false;
    if (f.asked && !e.asked_first) return false;
    return true;
}
const WHO_WORDS = { all: 'Everyone', claude: 'Claude', you: 'You' };
const STATE_WORDS = { any: 'Any', can: 'Can undo', refused: 'Undo refused', undone: 'Was undone', undos: 'Is an undo' };

/** The "Claude may write" switch as folio's status row (walk C1, C1.1): on,
 *  a quiet row; off, the whole card turns burnt orange and says so. */
function writeSwitchHTML(settings, h) {
    const on = settings.claude_may_write;
    const sw = `<button class="switch" role="switch" aria-checked="${on}" data-act="claude-switch" aria-label="Claude may write">
        <span class="track" aria-hidden="true"></span><span class="switch-word">${on ? 'On' : 'Off'}</span></button>`;
    if (!on) {
        return `<section class="card write-card is-off" aria-labelledby="write-head">
            <div class="write-row">
                <span class="write-icon" aria-hidden="true">${icon('prohibit')}</span>
                <div class="write-text"><h2 id="write-head">Claude may not write</h2>
                    <p>Claude can still read fin and answer in chat. Every write from chat is refused and changes nothing.</p>
                    ${settings.claude_write_changed_at ? `<p class="write-since">Off since ${esc(when(settings.claude_write_changed_at))}.</p>` : ''}</div>
                <div class="write-end">${sw}<button class="btn primary" data-act="claude-switch-on">Turn it back on</button></div>
            </div></section>`;
    }
    return `<section class="card write-card" aria-labelledby="write-head">
        <div class="write-row">
            <span class="write-key"><span class="write-icon" aria-hidden="true">${icon('chat')}</span><span id="write-head">Chat writes</span></span>
            <p class="write-text">Claude may write to fin straight from chat; each write lands here with Undo. Over ${esc(h.many_rows)} rows, Claude says the count in chat and waits for your yes.</p>
            <div class="write-end">${sw}</div>
        </div></section>`;
}

async function viewChanges() {
    const [h, settings, r, services] = await Promise.all([get('/api/history?limit=300&blockers=1', { fresh: true }), get('/api/settings', { fresh: true }), refs(), servicesList()]);
    const ctx = { r, services };
    if (!S.onChanges) {
        // The divider stays where the mark was when the page was opened; opening it is looking.
        S.onChanges = true;
        S.lastSeenMark = h.last_looked;
        S.lastSeenAt = h.last_looked_at;
        if (h.newest && h.newest !== h.last_looked) {
            send('POST', '/api/changes/looked', { upto: h.newest }).then(() => refreshFrame());
        }
    }
    S.manyRows = h.many_rows;
    // Opened from a quiet mark: that change, open, in view.
    const wanted = Number(new URLSearchParams(location.hash.split('?')[1] || '').get('entry')) || null;
    if (wanted) S.changes.open.add(wanted);
    const mark = S.lastSeenMark;
    const entries = h.entries;
    const byId = new Map(entries.map(e => [e.id, e]));
    const shown = entries.filter(e => entryMatches(e, mark));
    const count = fn => entries.filter(fn).length;
    // The divider counts Claude's changes only (walk C5.1): your own writes are not news.
    const claudeNew = count(e => e.via === 'chat' && e.id > (mark || 0));
    let dividerDone = mark === null;
    let lastDay = null;
    const items = shown.map((e, i) => {
        let lead = '';
        if (!dividerDone && e.id <= mark) {
            dividerDone = true;
            if (i > 0) {
                lead += `<li class="divider" role="separator"><span>${S.lastSeenAt ? `You last looked ${esc(timeWords(S.lastSeenAt))}` : 'You last looked here'}${claudeNew ? ` · ${plural(claudeNew, 'change')} by Claude ${claudeNew === 1 ? 'is' : 'are'} new above` : ''}</span></li>`;
            }
        }
        const d = localDay(e.at);
        if (d !== lastDay) { lastDay = d; lead += `<li class="day-head" role="presentation">${esc(dayHeading(d))}</li>`; }
        const open = S.changes.open.has(e.id);
        const amount = e.rows === 1 && e.first && e.first.table === 'transactions' ? txAmount(e.first) : '';
        const meta = [
            `${whoTag(e)} <span>${esc(whereFrom(e))}</span>`,
            esc(when(e.at)),
            plural(e.rows, 'row'),
            amount ? `<span class="num">${esc(amount)}</span>` : '',
            e.undone_by ? `undone by ${esc(whoName(e.undone_by_who, { cap: false }))} ${esc(e.undone_by_who ? timeWords(e.undone_by_who.at) : '')}` : '',
        ].filter(Boolean).join(' <span class="sep" aria-hidden="true">·</span> ');
        return `${lead}<li class="entry${e.via === 'chat' ? ' claude' : ''}${e.undone_by ? ' undone' : ''}${e.blocked_by ? ' refused' : ''}" id="entry-${e.id}">
            <div class="entry-row">
                <label class="sel"><input type="checkbox" data-change="entry-tick" data-entry="${e.id}" aria-label="Select this change"${e.undone_by || e.blocked_by ? ' disabled' : ''}></label>
                <div class="entry-main">
                    <button class="entry-title" data-act="entry-open" data-entry="${e.id}" aria-expanded="${open}" aria-controls="detail-${e.id}">${icon('caret-right', 'caret')}<span>${entryTitle(e, ctx, byId)}</span></button>
                    <div class="entry-meta">${meta}${e.asked_first ? ' ' + tag('asked', 'asked first, yes in chat') : ''}</div>
                    ${e.blocked_by ? `<div class="notice bad refusal">${icon('prohibit')}<div><b>Undo refused.</b> ${refusalWords(e, ctx)}
                        <button class="btn sm" data-act="trace" data-entry="${e.id}" data-blocker="${e.blocked_by.id}">Show where</button></div></div>` : ''}
                </div>
                <div class="act">${entryState(e)}</div>
            </div>
            <div class="entry-detail" id="detail-${e.id}"${open ? '' : ' hidden'}>${open ? '<p class="loading">Loading…</p>' : ''}</div></li>`;
    }).join('');
    afterRender(() => {
        S.changes.open.forEach(id => fillEntry(id));
        if (wanted) $(`#entry-${wanted}`)?.scrollIntoView({ block: 'center' });
        tickedChanged();
    });

    const f = S.changes;
    const chip = (k, v, label, n) => `<button class="chip" data-act="changes-filter" data-k="${k}" data-v="${v}" aria-pressed="${String(f[k]) === String(v)}">${label}${n !== undefined ? ` <span class="n">${n}</span>` : ''}</button>`;
    const newCount = count(e => e.id > (mark || 0));
    // On a phone the filters fold behind one button that names what is on (walk C2.1).
    const active = [f.who !== 'all' ? WHO_WORDS[f.who] : '', f.state !== 'any' ? STATE_WORDS[f.state] : '', f.newOnly ? 'New' : '', f.asked ? 'Asked first' : ''].filter(Boolean);
    return `<div class="page-head"><div><span class="eyebrow">Changes</span><h1>Recent changes</h1><p>Every write, yours and Claude’s, in one undoable list. There is no confirm screen: you check here, and undo.</p></div></div>
    ${writeSwitchHTML(settings, h)}
    <section class="card changes-card">
        <button class="chip filters-fold" data-act="changes-fold" aria-expanded="${!!f.folded}" aria-controls="change-filters">${icon('funnel')} Filters${active.length ? ` · ${esc(active.join(', '))}` : ''}</button>
        <div class="filter-set${f.folded ? ' is-open' : ''}" id="change-filters">
            <div class="filter-group"><span class="filter-label">Who</span><div class="chips">${chip('who', 'all', 'Everyone', entries.length)}${chip('who', 'claude', 'Claude', count(e => e.via === 'chat'))}${chip('who', 'you', 'You', count(e => e.via !== 'chat'))}</div></div>
            <div class="filter-group"><span class="filter-label">Undo</span><div class="chips">${chip('state', 'any', 'Any')}${chip('state', 'can', 'Can undo', count(e => !e.undone_by && !e.blocked_by))}${chip('state', 'refused', 'Undo refused', count(e => e.blocked_by))}${chip('state', 'undone', 'Was undone', count(e => e.undone_by))}${chip('state', 'undos', 'Is an undo', count(e => e.undoes))}</div></div>
            <div class="filter-group"><span class="filter-label">Also</span><div class="chips"><button class="chip" data-act="changes-toggle" data-k="newOnly" aria-pressed="${f.newOnly}">New since you last looked <span class="n">${newCount}</span></button>
                <button class="chip" data-act="changes-toggle" data-k="asked" aria-pressed="${f.asked}">Asked first in chat <span class="n">${count(e => e.asked_first)}</span></button></div></div>
        </div>
        <div class="log-bar"><span class="small muted">${shown.length} of ${plural(entries.length, 'change')}. Open one to see each row before and after.</span>
            <button class="btn primary" data-act="batch-undo" id="batch-undo" hidden>Undo selected</button></div>
        <ul class="log">${items || '<li class="empty">No changes match.</li>'}</ul>
        <p class="card-foot">Undo is refused when a later change touched the same rows; the refusal says which. Undo selected runs newest first and stops at the first refusal. An undo is a change too, and can be undone.</p>
    </section>`;
}
window.addEventListener('hashchange', () => { if (!location.hash.startsWith('#/changes')) S.onChanges = false; });
ACT['changes-filter'] = el => { S.changes[el.dataset.k] = el.dataset.v; rerender(); };
ACT['changes-toggle'] = el => { S.changes[el.dataset.k] = !S.changes[el.dataset.k]; rerender(); };
ACT['changes-fold'] = el => {
    S.changes.folded = !S.changes.folded;
    el.setAttribute('aria-expanded', String(S.changes.folded));
    $('#change-filters')?.classList.toggle('is-open', S.changes.folded);
};
// "Undo selected" shows only once something is ticked, and says how many (walk C2.2).
function tickedChanged() {
    const n = $$('.log input[data-change="entry-tick"]:checked').length;
    const b = $('#batch-undo');
    if (!b) return;
    b.hidden = n === 0;
    b.textContent = n ? `Undo ${n} selected` : 'Undo selected';
}
ACT['entry-tick'] = () => tickedChanged();
async function setClaudeWrite(on) {
    const r = await send('PUT', '/api/settings/claude-write', { on });
    if (!r.ok) { toast(r.data.error || 'That did not work', { bad: true }); return; }
    toast(on ? 'Claude may write again.' : 'Claude’s writes are off. Reads still work.');
    rerender();
}
ACT['claude-switch'] = el => setClaudeWrite(el.getAttribute('aria-checked') !== 'true');
ACT['claude-switch-on'] = () => setClaudeWrite(true);
ACT['entry-open'] = el => {
    const id = Number(el.dataset.entry);
    const box = $(`#detail-${id}`);
    if (S.changes.open.has(id)) { S.changes.open.delete(id); box.hidden = true; el.setAttribute('aria-expanded', 'false'); return; }
    S.changes.open.add(id); box.hidden = false; el.setAttribute('aria-expanded', 'true');
    box.innerHTML = '<p class="loading">Loading…</p>';
    fillEntry(id);
};

function rowLabel(table, row, ctx) {
    if (!row) return esc(table);
    if (table === 'transactions') return `${esc(row.description)} <span class="muted">· ${esc(day(row.date, { year: false }))}</span>`;
    if (table === 'anchors') return `figure of ${esc(day(row.date))}`;
    if (table === 'services') return `merchant ${esc(row.name)}`;
    if (table === 'merchant_rules') return `rule ${esc(row.pattern)}`;
    if (table === 'accounts') return `account ${esc(row.name)}`;
    if (table === 'statements') return `statement of ${esc(day(row.statement_date))}`;
    if (table === 'subscriptions') return `bill ${esc(ctx.services.find(s => s.id === row.service_id)?.name || row.match_pattern || '')}`;
    if (table === 'rates') return `rate ${esc(row.pair)} ${esc(day(row.date))}`;
    return esc(table);
}
function beforeCell(c, k, ctx) {
    const was = valueText(c.table, k, (c.before || {})[k], ctx, c.currency);
    const now = valueText(c.table, k, (c.after || {})[k], ctx, c.currency);
    if (!was) return `<span class="empty-val">${emptyText(k)}</span>`;
    // A value actually taken away keeps its red strike; a value replaced is struck quietly.
    return `<span class="was${now ? '' : ' gone'}">${was}</span>`;
}
function afterCell(c, k, ctx) {
    const now = valueText(c.table, k, (c.after || {})[k], ctx, c.currency);
    return now ? `<span class="now">${now}</span>` : `<span class="empty-val">${emptyText(k)}</span>`;
}
/** A change opened (walk C4.1): one line per field, with the row's account
 *  and amount, before and after in their own columns. */
async function changeRowsHTML(entry) {
    const ctx = { r: await refs(), services: await servicesList() };
    const changes = entry.changes;
    const out = [];
    const txRows = new Set(changes.filter(c => c.table === 'transactions').map(c => c.row_id));
    for (const c of changes.slice(0, 60)) {
        const row = c.after || c.before;
        const name = c.table === 'transactions' && txRows.size > 1
            ? `<button class="link" data-act="row" data-tx="${c.row_id}">${esc(row.description)}</button> <span class="muted">· ${esc(day(row.date, { year: false }))}</span>`
            : rowLabel(c.table, row, ctx);
        const acct = esc(accountOf(c, ctx));
        const amt = c.table === 'transactions' ? esc(txAmount(c)) : c.table === 'anchors' ? valueText('anchors', 'amount', row.amount, ctx, c.currency) : '';
        const lines = c.op === 'update' ? changedFields(c).map(k => [esc(FIELD_WORDS[k] || k.replace(/_/g, ' ')), beforeCell(c, k, ctx), afterCell(c, k, ctx)])
            : c.op === 'insert' ? [['added', '<span class="empty-val">not there</span>', '<span class="now">added</span>']]
            : [['removed', '<span class="was gone">there</span>', '<span class="empty-val">removed</span>']];
        if (!lines.length) lines.push(['how it is labelled', '<span class="empty-val">no field you see changed</span>', '']);
        lines.forEach(([field, was, now], i) => {
            out.push(`<tr${i ? ' class="cont"' : ''}><td class="c-row">${i ? '' : name}${!i && acct ? `<span class="c-sub">${acct}</span>` : ''}</td><td class="c-acct">${i ? '' : acct}</td>
                <td class="c-field">${field}</td><td class="c-was">${was}</td><td class="c-now">${now}</td><td class="c-amt r num">${i ? '' : amt}</td></tr>`);
        });
    }
    const more = changes.length > 60 ? `<p class="small muted">+ ${changes.length - 60} more rows.</p>` : '';
    const one = txRows.size === 1 ? changes.find(c => c.table === 'transactions') : null;
    const open = one ? `<p class="detail-foot"><button class="link" data-act="row" data-tx="${one.row_id}">Open ${esc(rowName(one))}</button> for its note, one-off and its own history</p>` : '';
    return `<div class="table-shell"><table class="t table-tight diff-table"><thead><tr><th>Row</th><th class="c-acct">Account</th><th>What changed</th><th>Before</th><th>After</th><th class="r c-amt">Amount</th></tr></thead>
        <tbody>${out.join('')}</tbody></table></div>${more}${open}`;
}
async function fillEntry(id) {
    const box = $(`#detail-${id}`);
    if (!box) return;
    try {
        const entry = await get(`/api/history/${id}`, { fresh: true });
        // The time and who are on the line above; only what is new here (walk C4.1).
        const asked = entry.asked_first ? `<p class="notice claude">${icon('chat')}<span>Over ${esc(S.manyRows ?? 'the')} rows: Claude said the count (${esc(entry.asked_count)}) in chat and you said yes.</span></p>` : '';
        box.innerHTML = `${asked}${await changeRowsHTML(entry)}<p class="change-no">change ${entry.id}</p>`;
    } catch (err) { box.innerHTML = `<p class="notice bad">${icon('warning-circle')}<span>${esc(err.message)}</span></p>`; }
}

async function undoEntry(id, { quiet = false } = {}) {
    const r = await send('POST', `/api/history/${id}/undo`);
    if (!r.ok) {
        if (!quiet) toast(r.data.error || 'Undo refused; nothing was changed', { bad: true, ms: 9000 });
        refreshFrame();
        if (location.hash.startsWith('#/changes')) rerender();
        return null;
    }
    if (!quiet) toast('Undone. The undo is in Recent changes, and can itself be undone.', { undo: r.data.by });
    refreshFrame();
    rerender();
    return r.data;
}
ACT.undo = el => undoEntry(Number(el.dataset.entry));
ACT['batch-undo'] = async () => {
    const ids = $$('.log input[data-change="entry-tick"]:checked').map(x => Number(x.dataset.entry)).sort((a, b) => b - a);
    if (!ids.length) return;
    let done = 0;
    for (const id of ids) {
        const r = await send('POST', `/api/history/${id}/undo`);
        if (!r.ok) { toast(`Undid ${done} of ${ids.length}. One was refused: ${r.data.error || ''}`, { bad: true, ms: 10000 }); break; }
        done += 1;
    }
    if (done === ids.length) toast(`Undid ${plural(done, 'change')}. Each undo is in the list and can itself be undone.`);
    refreshFrame(); rerender();
};

// A refused undo, traced: the rows the blocker shares with the entry, and
// that row's own timeline with the blocker marked.
ACT.trace = async el => {
    const id = Number(el.dataset.entry), blockerId = Number(el.dataset.blocker);
    const [entry, blocker] = await Promise.all([get(`/api/history/${id}`, { fresh: true }), get(`/api/history/${blockerId}`, { fresh: true })]);
    const theirs = new Set(blocker.changes.map(c => `${c.table}:${c.row_id}`));
    const shared = entry.changes.find(c => theirs.has(`${c.table}:${c.row_id}`));
    if (!shared) { toast('The rows they share could not be found'); return; }
    if (shared.table === 'transactions') return openRowSheet(shared.row_id, { blocker: blockerId, blocked: id });
    openTimeline(shared.table, shared.row_id, { blocker: blockerId, blocked: id });
};

/** Every change to one row, newest first, each opened (at most 12). */
async function rowHistory(table, rowId) {
    const list = (await get(`/api/history/row/${table}/${rowId}`, { fresh: true })).entries;
    const details = await Promise.all(list.slice(0, 12).map(e => get(`/api/history/${e.id}`, { fresh: true })));
    return { list, details };
}
/** One step of a row's own history: only what changed on this row, one line
 *  per field (walk C7.2); no repeated name, no link to the sheet you are in. */
function stepLines(e, table, rowId, ctx) {
    const mine = e.changes.filter(c => c.table === table && c.row_id === rowId);
    const lines = [];
    for (const c of mine) {
        if (c.op === 'insert') {
            const amt = table === 'transactions' ? txAmount(c) : '';
            if (amt) lines.push(`<div class="diff-line"><span class="num">${esc(amt)}</span></div>`);
        } else if (c.op === 'update') {
            for (const k of changedFields(c)) {
                lines.push(`<div class="diff-line"><span class="field">${esc(FIELD_WORDS[k] || k.replace(/_/g, ' '))}</span> ${beforeCell(c, k, ctx)} ${icon('caret-right', 'to')} ${afterCell(c, k, ctx)}</div>`);
            }
        } else lines.push('<div class="diff-line"><span class="was gone">taken out</span></div>');
    }
    return lines.join('');
}
function stepTitle(e, table, rowId, ctx) {
    const mine = e.changes.find(c => c.table === table && c.row_id === rowId);
    if (!mine) return esc(e.summary || 'A change');
    if (mine.op === 'insert' && table === 'transactions') {
        const acct = accountOf(mine, ctx);
        return `Came in${acct ? ` with the ${esc(acct)} statement` : ''}`;
    }
    if (e.undoes) return 'Undid a change to it';
    return sentence(changeWords({ ...mine, same_change: true, same_label: true }, ctx, { rows: 1, subject: 'it' })) || esc(e.summary || 'A change');
}
async function timelineHTML(table, rowId, { blocker = null, blocked = null } = {}, held = null) {
    const { list, details } = held || await rowHistory(table, rowId);
    const ctx = { r: await refs(), services: await servicesList() };
    let refusedOne = blocked ? details.find(d => d.id === blocked) : null;
    if (blocked && !refusedOne) refusedOne = await get(`/api/history/${blocked}`, { fresh: true }).catch(() => null);
    const items = details.map(e => {
        const isBlocker = e.id === blocker;
        const head = `<div class="step-meta">${esc(when(e.at))} ${whoTag(e)} <span>${esc(whereFrom(e))}</span>${e.undone_by ? ' <span class="state-word">· undone</span>' : ''}</div>
            <div class="step-title">${stepTitle(e, table, rowId, ctx)}</div>${stepLines(e, table, rowId, ctx)}`;
        if (isBlocker) {
            // The fix sits at the blocker (walk C7.1, option A): undo only this one.
            const theirs = refusedOne ? `${whoseName(refusedOne) === 'your' ? 'your' : 'Claude’s'} change ${esc(timeWords(refusedOne.at))}` : 'that change';
            return `<li class="step blocker"><div class="notice bad">${icon('prohibit')}<div>${head}
                <p class="why">This is why ${theirs} cannot be undone: ${esc(whoName(e, { cap: false }))} changed this row after it.</p>
                ${e.undone_by ? '' : `<button class="btn sm" data-act="undo-blocker" data-entry="${e.id}" data-table="${esc(table)}" data-row="${rowId}">Undo only this</button>`}</div></div></li>`;
        }
        return `<li class="step${e.via === 'chat' ? ' claude' : ''}${e.id === blocked ? ' was-refused' : ''}">${head}
            ${e.id === blocked ? `<p class="refused-note">${icon('prohibit')} The change whose undo was refused.</p>` : ''}</li>`;
    });
    return items.length ? `<ul class="timeline">${items.join('')}</ul>${list.length > 12 ? `<p class="small muted">+ ${list.length - 12} older.</p>` : ''}`
        : '<p class="small muted">No change has touched it since it came in.</p>';
}
ACT['undo-blocker'] = async el => {
    const r = await send('POST', `/api/history/${Number(el.dataset.entry)}/undo`);
    if (!r.ok) { toast(r.data.error || 'Undo refused; nothing was changed', { bad: true, ms: 9000 }); return; }
    toast('Undone. The change it blocked can now be undone from the list.', { undo: r.data.by });
    refreshFrame();
    if (location.hash.startsWith('#/changes')) rerender();
    const table = el.dataset.table, rowId = Number(el.dataset.row);
    if (table === 'transactions') openRowSheet(rowId); else openTimeline(table, rowId);
};
async function openTimeline(table, rowId, opts) {
    openSheet('Its own history', '<p class="loading">Loading…</p>', { eyebrow: 'One row', sub: 'Newest first.' });
    sheetBody().innerHTML = await timelineHTML(table, rowId, opts);
}

const LABEL_SOURCE_WORDS = { manual: 'by hand', service_default: 'its merchant', rule_override: 'a rule', fallback: 'its wording', derived: 'worked out from figures', auto: 'the rules' };
const LABEL_FIELDS = ['type_id', 'service_id', 'flow_type', 'book'];
/** Who labelled a row and when, from its own history (walk C8.2). */
function labelledBy(found, details) {
    for (const d of details) {
        if (d.undone_by) continue;
        const c = d.changes.find(x => x.table === 'transactions' && x.row_id === found.id && x.op === 'update');
        if (c && changedFields(c).some(k => LABEL_FIELDS.includes(k))) return `${whoName(d)}${d.undoes ? ', by an undo' : ''}, ${when(d.at)}`;
    }
    return found.cat_source ? (LABEL_SOURCE_WORDS[found.cat_source] || 'the rules') : 'not yet';
}

// One row: its amount, its facts, its note, the one-off toggle, "This was…", and its history.
ACT.row = el => openRowSheet(Number(el.dataset.tx));
async function openRowSheet(txId, opts = {}) {
    openSheet('A row', '<p class="loading">Loading…</p>', { eyebrow: 'One row' });
    const found = (await get(`/api/transactions?tx_id=${txId}`, { fresh: true })).transactions[0];
    if (!found) { sheetBody().innerHTML = '<p class="notice">This row is no longer in the books (an import was undone, perhaps).</p>' + await timelineHTML('transactions', txId, opts); return; }
    ACT.__lastRow = found;
    const held = await rowHistory('transactions', txId);
    const waiting = found.flow_type === 'review';
    const untyped = !found.display_type && ['expense', 'refund'].includes(found.flow_type);
    const label = waiting ? tag('nofig', 'waiting for a label') : found.display_type ? esc(found.display_type) : (untyped ? tag('nofig', 'no type') : esc(FLOW_WORDS[found.flow_type] || found.flow_type));
    const minor = toMinor(found.amount_sgd, found.currency);
    $('#sheet-title').textContent = found.description;
    // "This was…" is the row's one main action only while it waits for a label (walk C8.3).
    const thisWas = waiting || untyped
        ? `<button class="btn ink" data-act="${waiting ? 'this-was' : 'resolve'}" data-tx="${found.id}">This was…</button>`
        : `<button class="link" data-act="resolve" data-tx="${found.id}">Change what this was…</button>`;
    sheetBody().innerHTML = `<div class="row-figure-block">
            <div class="row-figure num">${esc(money(-minor, found.currency, { signed: true }))}</div>
            <p class="row-figure-sub">${minor > 0 ? 'Money out' : 'Money in'} · ${esc(day(found.date))}${found.account_name ? ' · ' + esc(found.account_name) : ''}</p></div>
        <dl class="facts">
            <div><dt>Book</dt><dd>${esc(found.book || '—')}</dd></div>
            <div><dt>Type</dt><dd>${label}</dd></div>
            ${found.other_side_name ? `<div><dt>Other side</dt><dd>${esc(found.other_side_name)}</dd></div>` : ''}
            <div><dt>Merchant</dt><dd>${found.service_name ? esc(found.service_name) : '<span class="muted">none yet</span>'}</dd></div>
            <div><dt>Labelled by</dt><dd>${esc(labelledBy(found, held.details))}</dd></div>
        </dl>
        <label class="check"><input type="checkbox" data-change="row-oneoff" data-tx="${found.id}"${found.is_one_off ? ' checked' : ''}> One-off <span class="muted small">(Spending can leave it out)</span></label>
        <label class="field"><span>Note</span><textarea id="row-note">${esc(found.notes || '')}</textarea></label>
        <div class="row row-actions"><button class="btn" data-act="row-note" data-tx="${found.id}">Save the note</button>${thisWas}</div>
        <h3 class="history-head">Its own history</h3><div id="row-history"><p class="loading">Loading…</p></div>`;
    $('#row-history').innerHTML = await timelineHTML('transactions', txId, opts, held);
    if (opts.blocker) $('#row-history .blocker')?.scrollIntoView({ block: 'center' });
}
ACT['row-note'] = async el => {
    const r = await act('PUT', `/api/transactions/${el.dataset.tx}`, { notes: $('#row-note').value.trim() || null }, 'Note saved');
    if (r) openRowSheet(Number(el.dataset.tx));
};
ACT['row-oneoff'] = async el => {
    const r = await act('PUT', `/api/transactions/${el.dataset.tx}`, { is_one_off: el.checked ? 1 : 0 }, el.checked ? 'Marked one-off' : 'No longer one-off');
    if (r) openRowSheet(Number(el.dataset.tx));
};

// ---------------------------------------------------------------------------
// Rates: a balance in another currency joins the S$ totals at the saved rate
// for its day. Fetch the reference rate, or enter one in place of it. Opened
// from a line or margin note with no rate, and from Lists.
// ---------------------------------------------------------------------------

ACT.rate = el => openRate(el.dataset.currency || 'INR', el.dataset.date || rateDay());
function openRate(currency, date) {
    openSheet(`${esc(currency)} rate`, `<p class="small">A balance in ${esc(currency)} joins the S$ totals at the rate saved for its day, or the latest saved before it. With none, it is left out.</p>
        <div class="fields two"><label class="field"><span>Currency</span><select id="rt-cur">${['INR', 'USD', 'EUR', 'GBP', 'AUD'].map(c => `<option${c === currency ? ' selected' : ''}>${c}</option>`).join('')}</select></label>
            <label class="field"><span>For the day</span><input type="date" id="rt-date" value="${esc(date)}" max="${todayIso()}"></label></div>
        <div class="rate-way"><h3>${icon('arrows-clockwise')}Fetch the reference rate</h3>
            <p class="hint">The European Central Bank's rate for that day; a weekend or holiday takes the business day before, and the saved rate says so. A rate already saved for the day is kept.</p>
            <button class="btn primary block" data-act="rate-fetch">Fetch the reference rate</button></div>
        <div class="rate-way"><h3>${icon('pencil-simple')}Or enter it</h3>
            <label class="field"><span>S$ for 1 <b id="rt-unit">${esc(currency)}</b></span><input type="text" inputmode="decimal" id="rt-rate" placeholder="for example 0.0153"></label>
            <p class="hint">An entered rate takes the place of any saved for that day. Every S$ figure is worked out on read, so the months that use it follow.</p>
            <button class="btn block" data-act="rate-enter">Save this rate for the day</button></div>`, { eyebrow: 'Books · Rates', sub: `for ${esc(dateWords(date))}` });
    $('#rt-cur').addEventListener('change', () => { $('#rt-unit').textContent = $('#rt-cur').value; });
}
ACT['rate-fetch'] = async () => {
    const body = { currency: $('#rt-cur').value, date: $('#rt-date').value };
    const r = await act('POST', '/api/rates/fetch', body, 'Rate saved');
    if (r) {
        const held = r.data.rate || {};
        toast(r.data.created ? `Saved: 1 ${body.currency} = S$ ${held.rate} (${day(held.date)})` : `Already saved for ${day(held.date)}: 1 ${body.currency} = S$ ${held.rate}`);
        closeSheet(); rerender();
    }
};
ACT['rate-enter'] = async () => {
    const rate = $('#rt-rate').value.trim();
    if (!rate) { toast('Enter the rate, S$ for one unit', { bad: true }); return; }
    const r = await act('PUT', '/api/rates', { currency: $('#rt-cur').value, date: $('#rt-date').value, rate }, 'Rate entered');
    if (r) { closeSheet(); rerender(); }
};

// ---------------------------------------------------------------------------
// The quiet mark's link: that change, opened, in Changes
// ---------------------------------------------------------------------------

ACT['goto-change'] = el => {
    const id = Number(el.dataset.entry);
    closeSheet();
    S.changes = { ...S.changes, who: 'all', state: 'any', newOnly: false, asked: false };
    S.changes.open.add(id);
    location.hash = `#/changes?entry=${id}`;
};

// ---------------------------------------------------------------------------
// The backup warning (fin-online D3): shown quietly in the shell when backups
// are not configured (hosted only, never in local-dev) or none has succeeded
// in 36 hours. The server decides; this only shows it.
// ---------------------------------------------------------------------------

document.addEventListener('DOMContentLoaded', async () => {
    try {
        const res = await fetch('/api/backups/status', { headers: { Accept: 'application/json' } });
        if (!res.ok) return;
        const status = await res.json();
        if (!status.warning) return;
        const banner = document.createElement('div');
        banner.className = 'banner backup-warning';
        banner.setAttribute('role', 'status');
        banner.innerHTML = `${icon('warning-circle')}<span class="banner__message"><b>Backups:</b> ${esc(status.warning)}${status.last_success_at ? ` (last success ${esc(status.last_success_at)})` : ''}</span>`;
        $('#banners')?.appendChild(banner);
    } catch (_) { /* no banner if the status cannot be read */ }
});
