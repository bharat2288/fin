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
        return tag('nofig', 'no figure', fixFor(line));
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
    if (line.check?.status === 'ties') return '<span class="tick ties" title="ties">✓</span>';
    if (line.check?.status === 'off') return '<span class="tick off" title="off">≠</span>';
    if (line.check?.status === 'not_checked') return '<span class="tick notchecked" title="not checked">○</span>';
    if (line.rests_on?.source === 'supplied') return '<span class="tick yours" title="your figure">†</span>';
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

async function viewHome() {
    const month = currentMonth();
    const prev = prevMonth(month);
    const [settings, now, before, q, cards, r] = await Promise.all([
        get('/api/settings'), sheetFor(month),
        prev >= SHEET_START ? sheetFor(prev).catch(() => null) : Promise.resolve(null),
        loadQueue(), get('/api/dashboard/stat-cards'), refs(),
    ]);

    const claude = settings.claude_count
        ? `<div class="claude-line"><span class="spark" aria-hidden="true">✳</span>
            <span class="grow"><b>${plural(settings.claude_count, 'change')} by Claude</b> since you last looked${settings.latest_claude_at ? ` · latest ${esc(when(settings.latest_claude_at))}` : ''}</span>
            <a class="btn sm" href="#/changes">Check them</a>
            <button class="btn sm quiet" data-act="looks-right" data-upto="${settings.newest}">Looks right</button></div>`
        : `<div class="claude-line quiet"><span aria-hidden="true">✳</span><span class="grow">Nothing new from Claude since you last looked.</span>
            <span class="small">Claude may write: <b>${settings.claude_may_write ? 'on' : 'off'}</b></span></div>`;

    const chips = [];
    if (before) {
        const change = now.net_worth_minor - before.net_worth_minor;
        chips.push(`<span class="chip" style="cursor:default"><span class="num">${esc(money(change, 'SGD', { signed: true }))}</span> since ${esc(day(before.as_at, { year: false }))}</span>`);
        const mc = before.month_check;
        if (mc && mc.available && mc.unexplained_minor !== null && mc.unexplained_minor !== undefined) {
            chips.push(mc.unexplained_minor === 0
                ? tag('ties', `${monthName(prev, { short: true })} adds up`, { href: '#/books' + '', title: 'The month check' })
                : `<a class="tag unexplained" href="#/books" data-act="go-month-check" data-month="${prev}">${icon('warning-circle')}${esc(money(mc.unexplained_minor, 'SGD'))} unexplained in ${esc(monthName(prev, { short: true }))}</a>`);
        }
    }
    now.left_out.forEach(l => {
        const line = now.sections.flatMap(s => s.lines).find(x => x.name === l.name);
        chips.push(tag('nofig', `${l.name}: ${l.why}, left out`, line ? fixFor(line, now.as_at) : {}));
    });

    const hero = `<section class="card home-hero">
        <div class="spread"><span class="eyebrow">Net worth · today</span><a class="link small" href="#/books">Balance sheet</a></div>
        <div class="hero-figure num">${heroFigure(now.net_worth_minor, now.currency)}</div>
        <div class="chips">${chips.join('')}</div>
        ${restsOnHTML(now)}</section>`;

    const lane = (key, title, list, sum) => `<div class="lane">
        <h3>${key === 'out' ? dirTag('out') : key === 'in' ? dirTag('in') : ''} ${title}
            <span class="lane-sum">${plural(list.length, 'item')}${sum ? ` · transfers ${esc(sum)}` : ''}</span></h3>
        ${list.length ? list.slice(0, 3).map(itemHTML).join('') : '<p class="muted small">Nothing waits here.</p>'}
        ${list.length > 3 ? `<a class="link small" href="#/queue">${list.length - 3} more in the queue</a>` : ''}</div>`;
    const waits = `<section class="card home-waits">
        <div class="card-head"><h2>Waiting for you</h2><a class="btn sm" href="#/queue">Open the queue · ${q.count}</a></div>
        ${q.capped.length ? `<p class="small notice">Too many to read at once, so these counts and sums are short: ${esc(q.capped.join('; '))}.</p>` : ''}
        ${q.count ? `<div class="waits-cols">${lane('out', 'Money out', q.lanes.out, q.sums.out)}${lane('in', 'Money in', q.lanes.in, q.sums.in)}</div>
        ${q.lanes.books.length ? `<div style="margin-top:16px">${lane('books', 'Figures and statements', q.lanes.books)}</div>` : ''}`
        : '<p class="empty">Nothing waits for you.</p>'}</section>`;

    const books = r.books.map(b => b.name);
    const tile = (cls, eyebrow, label, minor, body) => `<div class="tile ${cls}"><div class="eyebrow">${esc(eyebrow)}</div>
        <div class="label">${esc(label)}</div><div class="fig num">${esc(money(minor, 'SGD'))}</div><p>${body}</p></div>`;
    const tiles = [];
    books.forEach((name, i) => {
        const key = name.toLowerCase();
        if (!(key in cards)) return;
        const minor = toMinor(cards[key]);
        if (i === 0) {
            tiles.push(tile('household', 'Household', `${name} spending`, minor,
                `${plural(cards.household_rows || 0, 'row')}. Held out until labelled: out <b>${esc(money(toMinor(cards.held_out_out_total), 'SGD'))}</b> · in <b>${esc(money(toMinor(cards.held_out_in_total), 'SGD'))}</b>.`));
        } else {
            tiles.push(tile('company', 'Company book', `${name}, its costs`, minor,
                `Paid from our accounts. Not household spending; it moves ${esc(name)}’s company balance.`));
        }
    });
    tiles.push(tile('movement', 'Movement, not spending', 'Loan principal repaid', toMinor(cards.loan_principal),
        `What the loans fell by: instalments ${esc(money(toMinor(cards.loan_instalments), 'SGD'))} less interest ${esc(money(toMinor(cards.loan_interest), 'SGD'))}. Still the household’s money.`));
    const tilesCard = `<section class="card home-tiles">
        <div class="card-head"><h2>${esc(cards.ref_label)}: ${tiles.length} figures, never added</h2><a class="link small" href="#/books/spending">Spending</a></div>
        <div class="tiles">${tiles.join('')}</div>
        <p class="never">Each book is its own figure, drawn three different ways so they never read as one sum. No total is shown between them.</p></section>`;

    const mcCard = before ? monthCheckMini(before) : '';
    return `<div class="home">${claude}${hero}${mcCard}${waits}${tilesCard}</div>`;
}

function monthCheckMini(sheet) {
    const mc = sheet.month_check;
    if (!mc) return '';
    const name = monthName(sheet.month);
    if (!mc.available || mc.unexplained_minor === null) {
        return `<section class="card home-mc"><span class="eyebrow">Month check · ${esc(name)}</span><p class="small" style="margin-top:8px">${esc(mc.why || 'Not worked out for this month.')}</p></section>`;
    }
    const reasons = [];
    if (mc.review && mc.review.count) reasons.push(`${plural(mc.review.out_count, 'transfer')} out (${esc(money(mc.review.out_minor, 'SGD'))}) and ${mc.review.in_count} in (${esc(money(mc.review.in_minor, 'SGD'))}) wait for a label`);
    (mc.not_tying || []).forEach(l => reasons.push(`${esc(l.name)}: ${esc(l.text)}`));
    (mc.left_out || []).forEach(l => reasons.push(`${esc(l.name)} left out (${esc(l.why)})`));
    return `<section class="card home-mc"><span class="eyebrow">Month check · ${esc(name)}</span>
        <p class="small muted" style="margin:4px 0 10px">Start + income − spending + currency change, against the actual end.</p>
        <div class="eyebrow" style="margin-top:6px">${mc.unexplained_minor === 0 ? 'It adds up' : 'Unexplained'}</div>
        <div class="tile-fig num" style="font-family:var(--font-display);font-size:30px;font-weight:600;color:${mc.unexplained_minor === 0 ? 'var(--ok)' : 'var(--bad)'}">${esc(money(mc.unexplained_minor, 'SGD'))}</div>
        <p class="small muted">expected ${esc(money(mc.expected_minor, 'SGD'))} · actual ${esc(money(mc.actual_minor, 'SGD'))}</p>
        ${reasons.length ? `<ul class="small" style="padding-left:18px;margin:10px 0">${reasons.slice(0, 4).map(x => `<li>${x}</li>`).join('')}</ul>` : ''}
        <button class="btn sm" data-act="go-month-check" data-month="${sheet.month}">Open the month check</button></section>`;
}

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
    const rest = body.apply_scope !== 'transaction' && !body.pattern ? group.filter(x => x.id !== body.tx_id) : [];
    let done = 0;
    for (const x of rest) {
        const more = await send('POST', '/api/transactions/resolve', { ...body, tx_id: x.id, apply_scope: 'transaction', service_id: r.data.service_id ?? body.service_id });
        if (!more.ok) { toast(more.data.error || 'One of the rows could not be labelled', { bad: true }); break; }
        done += 1;
    }
    if (done) toast(`${plural(done, 'other row')} on this card took the same label`);
    else if (r.data.backfilled) toast(`${plural(r.data.backfilled, 'other matching row')} took the same label`);
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
const KIND_WORDS = { loan: 'a loan', holding: 'a holding', company: 'a company', person: 'a person', bank: 'a bank account', card: 'a card' };
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
            <div class="small">${esc(KIND_WORDS[a.type] || a.type)}, in ${esc(sign(a))}${owes(a) ? ' · owed' : ''}</div>${now}`;
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
        <label class="field" id="fg-pick"${named ? ' hidden' : ''}><span>Account</span><select id="fg-account">${takes.map(a => `<option value="${a.id}"${a.id === chosen.id ? ' selected' : ''}>${esc(a.name)}, ${esc(KIND_WORDS[a.type] || a.type)}, in ${esc(sign(a))}</option>`).join('')}</select></label>
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

const CHECK_ORDER = { off: 0, not_checked: 1, none: 2, ties: 3 };
function sortKey(line, key) {
    if (key === 'rests') return line.rests_on ? -line.rests_on.age_days : -99999;     // oldest first
    if (key === 'check') return CHECK_ORDER[line.check?.status || 'none'];
    if (key === 'value') return -(line.value_minor ?? -Infinity);
    return 0;
}

function marginNote(line, refused, asAt) {
    const notes = [];
    if (lacksRate(line)) notes.push(tag('nofig', `no ${line.currency} rate`, fixFor(line, asAt)));
    if (line.since) notes.push(`rolled forward: ${esc(line.since)}`);
    if (line.since_label && line.rows_since) notes.push(`${plural(line.rows_since, 'row')} ${esc(line.since_label)}`);
    if (line.made_of) notes.push(esc(line.made_of.text));
    if (line.rate) notes.push(esc(line.rate.text));
    if (line.left_out && line.balance !== 'no figure') notes.push(`left out: ${esc(line.left_out)}`);
    if (line.note) notes.push(esc(line.note));
    if (line.archived) notes.push('archived');
    if (refused) notes.push(`${tag(refused.set_aside ? 'aside' : 'refused', `${day(refused.statement_date, { year: false })} statement refused`, { act: 'refused-sum', data: { id: refused.id } })}`);
    return notes.join(' · ');
}

async function viewSheet() {
    const [sheet, q, r] = await Promise.all([sheetFor(S.month), loadQueue(), refs()]);
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

    const table = sheet.sections.map(section => {
        const lines = sorted(section.lines.filter(shown));
        if (!lines.length && S.needsLook) return '';
        const rows = lines.map(line => `<tr>
            <td><a href="#/books/account/${line.account_id}">${esc(line.name)}</a>${accountMark(line.account_id)}</td>
            <td class="r num">${line.currency !== 'SGD' && line.balance_minor !== null ? esc(money(line.balance_minor, line.currency)) : ''}</td>
            <td class="r num">${line.in_total ? esc(money(line.value_minor, 'SGD')) : line.counted_in ? '<span class="muted">counted above</span>' : tag('nofig', line.balance === 'no figure' ? 'no figure' : lacksRate(line) ? 'no rate' : 'left out', fixFor(line, sheet.as_at))}</td>
            <td class="tick">${tickOf(line)}</td>
            <td>${restsOn(line)}</td>
            <td>${checkMarker(line)}</td>
            <td class="margin-note">${marginNote(line, refusedBy.get(line.account_id), sheet.as_at)}</td></tr>`).join('');
        return `<tr class="section"><td colspan="7">${esc(section.heading)}${section.owed ? ' <span class="muted" style="text-transform:none;letter-spacing:0;font-weight:500">negative: what we owe</span>' : ''}</td></tr>${rows}
            ${S.needsLook ? '' : `<tr class="total"><td>${section.owed ? 'Total owed' : 'Total'}</td><td></td><td class="r num">${esc(money(section.total_minor, 'SGD'))}</td><td colspan="4" class="small muted">${section.left_out.length ? `left out: ${section.left_out.map(l => esc(l.name)).join(', ')}` : ''}</td></tr>`}`;
    }).join('');

    const cards = sheet.sections.map(section => {
        const lines = sorted(section.lines.filter(shown));
        if (!lines.length && S.needsLook) return '';
        return `<h3 class="eyebrow" style="margin-top:16px">${esc(section.heading)}</h3>${lines.map(line => `<div class="line-card">
            <div class="spread"><span><a href="#/books/account/${line.account_id}"><b>${esc(line.name)}</b></a>${accountMark(line.account_id)}</span>
            <span class="num"><b>${line.in_total ? esc(money(line.value_minor, 'SGD')) : line.counted_in ? '' : tag('nofig', line.balance === 'no figure' ? 'no figure' : lacksRate(line) ? 'no rate' : 'left out', fixFor(line, sheet.as_at))}</b></span></div>
            ${line.currency !== 'SGD' && line.balance_minor !== null ? `<div class="small num">${esc(money(line.balance_minor, line.currency))}</div>` : ''}
            <div class="small">${restsOn(line, { short: true })}</div>
            <div class="row small" style="margin-top:4px">${checkMarker(line)}</div>
            <div class="margin-note">${marginNote(line, refusedBy.get(line.account_id), sheet.as_at)}</div></div>`).join('')}
            ${S.needsLook ? '' : `<div class="spread small" style="padding:8px 0"><b>${section.owed ? 'Total owed' : 'Total'}</b><b class="num">${esc(money(section.total_minor, 'SGD'))}</b></div>`}`;
    }).join('');

    afterRender(() => drawBridge(sheet));
    return `${booksNav('sheet')}
    <div class="page-head"><div><span class="eyebrow">Balance sheet</span><h1>As at ${esc(day(sheet.as_at))}${S.month === currentMonth() ? ' <span class="muted small">(this month so far)</span>' : ''}</h1>
        <p>Every balance names what it rests on. What is owed is negative, under a heading that says owed. Amounts in S$; other currencies at the saved rate.</p></div>
        ${monthPicker()}</div>
    <section class="card">
        <div class="spread"><div><span class="eyebrow">Net worth</span><div class="hero-figure num" style="font-size:44px">${heroFigure(sheet.net_worth_minor, 'SGD')}</div></div>
            <div class="row"><button class="btn" data-act="figure">Enter a figure</button><a class="btn" href="#/queue">Queue · ${q.count}</a></div></div>
        ${sheet.note ? `<p class="small">${tag('nofig', 'left out')} ${esc(sheet.note)}</p>` : ''}
        ${restsOnHTML(sheet)}
    </section>
    <section class="card">
        <div class="spread" style="margin-bottom:10px"><h2>What we have and what we owe</h2>
            <div class="row"><div class="seg" role="group" aria-label="Which lines">
                <button data-act="needs-look" data-on="0" aria-pressed="${!S.needsLook}">All lines</button>
                <button data-act="needs-look" data-on="1" aria-pressed="${S.needsLook}">Needs a look · ${needCount}</button></div>
                <label class="field phone-only" style="min-width:160px"><select data-change="sheet-sort-select" aria-label="Sort">
                    <option value="">Sheet order</option><option value="rests"${S.sheetSort.key === 'rests' ? ' selected' : ''}>Rests on: oldest first</option>
                    <option value="check"${S.sheetSort.key === 'check' ? ' selected' : ''}>Check: worst first</option></select></label></div></div>
        <div class="table-wrap sheet-table"><table class="t">
            <thead><tr><th style="min-width:170px">Account</th><th class="r">In its currency</th><th class="r">${sortBtn('value', 'In S$')}</th><th></th><th>${sortBtn('rests', 'Rests on')}</th><th>${sortBtn('check', 'Check')}</th><th>Margin note</th></tr></thead>
            <tbody>${table || '<tr><td colspan="7" class="empty">Nothing needs a look.</td></tr>'}</tbody>
            ${S.needsLook ? '' : `<tfoot><tr class="total"><td>Net worth</td><td></td><td class="r num">${esc(money(sheet.net_worth_minor, 'SGD'))}</td><td colspan="4"></td></tr></tfoot>`}</table></div>
        <div class="sheet-lines">${cards || '<p class="empty">Nothing needs a look.</p>'}</div>
        ${sheet.currency_change && sheet.currency_change.minor ? `<p class="small" style="margin-top:12px">Currency change since ${esc(day(sheet.currency_change.from))}: <b class="num">${esc(money(sheet.currency_change.minor, 'SGD', { signed: true }))}</b></p>` : ''}
        ${sheet.currency_change && sheet.currency_change.minor === null && sheet.currency_change.note ? `<p class="small" style="margin-top:12px">${tag('nofig', 'currency change not worked out')} ${esc(sheet.currency_change.note)}
            ${[...new Set((sheet.currency_change.lines || []).map(l => l.currency).filter(Boolean))].map(c => `<button class="btn sm" data-act="rate" data-currency="${esc(c)}" data-date="${esc(rateDay(sheet.as_at))}">${esc(c)} rate</button>`).join(' ')}</p>` : ''}
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

function monthCheckHTML(sheet) {
    const mc = sheet.month_check;
    if (!mc) return '';
    const head = `<div class="card-head"><div><span class="eyebrow">Month check · ${esc(monthName(sheet.month))}</span>
        <h2>Do the month’s rows account for the change in net worth?</h2></div></div>`;
    if (!mc.available || mc.unexplained_minor === null || mc.unexplained_minor === undefined) {
        return `<section class="card" id="month-check">${head}<p>${esc(mc.why || 'Not worked out for this month.')}</p></section>`;
    }
    const line = (op, label, minor, cls = '') => `<tr class="${cls}"><td class="op">${op}</td><td>${label}</td><td class="r">${esc(money(minor, 'SGD'))}</td></tr>`;
    const signOp = m => (m < 0 ? '−' : '+');
    const unexplained = mc.unexplained_minor;
    const sum = `<table class="sum"><tbody>
        ${line('', `Net worth at ${esc(day(mc.from))} <span class="muted small">(the accounts in the check)</span>`, mc.opening_minor)}
        ${line('+', 'income', mc.income_minor)}
        ${line('−', `household spending${mc.interest_minor ? `, with ${esc(money(mc.interest_minor, 'SGD'))} loan interest` : ''}`, mc.spending_minor)}
        ${line(signOp(mc.currency_change_minor), 'currency change', Math.abs(mc.currency_change_minor))}
        ${mc.outside_minor ? line(signOp(mc.outside_minor), 'money moved to or from accounts left out', Math.abs(mc.outside_minor)) : ''}
        ${line('=', `what it should be at ${esc(day(mc.to))}`, mc.expected_minor, 'eq')}
        ${line('', 'what it is', mc.actual_minor)}
        <tr class="gap${unexplained === 0 ? ' zero' : ''}"><td class="op">${unexplained === 0 ? '✓' : '?'}</td><td>${unexplained === 0 ? 'it adds up' : 'unexplained'}</td><td class="r">${esc(money(unexplained, 'SGD'))}</td></tr>
        </tbody></table>`;
    const why = [];
    const rv = mc.review || {};
    if (rv.out_count) why.push(`<tr><td>${dirTag('out')} ${plural(rv.out_count, 'transfer')} waiting for a label</td><td class="r num">${esc(money(rv.out_minor, 'SGD'))}</td><td><a class="link" href="#/queue">Label them</a></td></tr>`);
    if (rv.in_count) why.push(`<tr><td>${dirTag('in')} ${plural(rv.in_count, 'transfer')} waiting for a label</td><td class="r num">${esc(money(rv.in_minor, 'SGD'))}</td><td><a class="link" href="#/queue">Label them</a></td></tr>`);
    (mc.not_tying || []).forEach(l => why.push(`<tr><td>${esc(l.name)}</td><td class="r">${l.status === 'off' ? tag('off', `off by ${money(Math.abs(l.difference_minor), l.currency)}`) : tag('notchecked', l.text)}</td><td><a class="link" href="#/books/account/${l.account_id}">See the tie line</a></td></tr>`));
    (mc.left_out || []).forEach(l => why.push(`<tr><td>${esc(l.name)} is left out of both sides</td><td class="r small">${esc(l.why)}</td><td><button class="link" data-act="figure">Enter a figure</button></td></tr>`));
    const whyTable = `<h3 style="margin:18px 0 6px">${unexplained === 0 ? 'Still worth a look' : 'Why it does not add up'}</h3>
        ${why.length ? `<div class="table-wrap"><table class="t"><thead><tr><th>Where to look</th><th class="r">How much</th><th>Fix</th></tr></thead><tbody>${why.join('')}</tbody></table></div>`
            : `<p class="small muted">${unexplained === 0 ? 'Nothing: every row is labelled and every line ties.' : 'Nothing waits and every line ties: a row may be missing or mislabelled, or a figure moved.'}</p>`}`;
    return `<section class="card" id="month-check">${head}
        <div class="grid-2"><div>${sum}${mc.note ? `<p class="small muted" style="margin-top:8px">${esc(mc.note)}</p>` : ''}</div>
        <div class="desk-only"><span class="eyebrow">The bridge</span><svg class="bridge" id="bridge" viewBox="0 0 520 240" role="img" aria-label="The month check as a bridge"></svg></div></div>
        ${whyTable}</section>`;
}

/** The month check as a bridge: from the opening figure, each step up or
 *  down, to what it should be, beside what it is. Desk only. */
function drawBridge(sheet) {
    const svg = $('#bridge');
    const mc = sheet.month_check;
    if (!svg || !mc || !mc.available || mc.unexplained_minor === null) return;
    const steps = [
        ['Start', mc.opening_minor, 'base'], ['Income', mc.income_minor, 'step'], ['Spending', -mc.spending_minor, 'step'],
        ['Currency', mc.currency_change_minor, 'step'],
    ];
    if (mc.outside_minor) steps.push(['Outside', mc.outside_minor, 'step']);
    steps.push(['Should be', mc.expected_minor, 'base'], ['Is', mc.actual_minor, 'actual']);
    let running = 0;
    const bars = steps.map(([label, v, kind]) => {
        if (kind === 'step') { const from = running; running += v; return { label, from, to: running, v, kind }; }
        if (kind === 'base' && label === 'Start') running = v;
        return { label, from: 0, to: v, v, kind };
    });
    const values = bars.flatMap(b => [b.from, b.to]);
    let lo = Math.min(...values), hi = Math.max(...values);
    // A bridge from zero would flatten the steps: start just below the lowest top.
    const tops = bars.filter(b => b.kind !== 'step').map(b => b.to);
    const floor = Math.min(...tops, ...bars.filter(b => b.kind === 'step').flatMap(b => [b.from, b.to]));
    lo = floor - (hi - floor) * 0.25;
    if (hi === lo) hi = lo + 1;
    const W = 520, H = 240, top = 18, bottom = 196, n = bars.length, bw = W / n;
    const y = v => bottom - ((v - lo) / (hi - lo)) * (bottom - top);
    const colour = b => b.kind === 'actual' ? 'var(--text-primary)' : b.kind === 'base' ? 'var(--border-emphasis)' : b.v >= 0 ? 'var(--ok)' : 'var(--terra)';
    let out = '';
    bars.forEach((b, i) => {
        const x = i * bw + bw * 0.18, w = bw * 0.64;
        const from = b.kind === 'step' ? b.from : lo;
        const y1 = y(Math.max(from, b.to)), y2 = y(Math.min(from, b.to));
        out += `<rect x="${x}" y="${y1}" width="${w}" height="${Math.max(2, y2 - y1)}" rx="3" fill="${colour(b)}"/>`;
        out += `<text x="${x + w / 2}" y="${bottom + 16}" text-anchor="middle">${esc(b.label)}</text>`;
        const shown = b.kind === 'step' ? money(b.v, 'SGD', { signed: true }) : money(b.v, 'SGD');
        out += `<text x="${x + w / 2}" y="${bottom + 32}" text-anchor="middle" style="font-size:10.5px">${esc(shown.replace('S$ ', ''))}</text>`;
    });
    const gap = mc.unexplained_minor;
    if (gap) {
        const xs = (n - 2) * bw + bw * 0.5, xe = (n - 1) * bw + bw * 0.5;
        out += `<line x1="${xs}" x2="${xe}" y1="${y(mc.expected_minor)}" y2="${y(mc.expected_minor)}" stroke="var(--bad)" stroke-dasharray="4 3"/>`;
        out += `<text x="${(xs + xe) / 2}" y="${Math.min(y(mc.expected_minor), y(mc.actual_minor)) - 6}" text-anchor="middle" style="fill:var(--bad);font-weight:700">? ${esc(money(gap, 'SGD'))}</text>`;
    }
    svg.innerHTML = out;
}

// ---------------------------------------------------------------------------
// One account's page: its balance, its tie line, its figures and rows
// ---------------------------------------------------------------------------

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

    let tieLine = '<p class="muted">No balance yet: nothing to rest on.</p>';
    if (line && line.rests_on && line.balance_minor !== null) {
        const anchor = anchorsList.find(a => a.date === line.rests_on.date);
        const base = anchor ? anchor.amount_minor : null;
        const change = base !== null ? line.balance_minor - base : null;
        tieLine = `<table class="sum"><tbody>
            <tr><td class="op"></td><td>${line.rests_on.source === 'supplied' ? 'your figure' : 'statement'} ${esc(day(line.rests_on.date))}</td><td class="r">${esc(money(base, cur))}</td></tr>
            ${change !== null ? `<tr><td class="op">${change < 0 ? '−' : '+'}</td><td>${esc(line.since || 'nothing since')}</td><td class="r">${esc(money(Math.abs(change), cur))}</td></tr>` : ''}
            <tr class="eq"><td class="op">=</td><td>balance at ${esc(day(sheet.as_at))}</td><td class="r">${esc(money(line.balance_minor, cur))}</td></tr>
            ${cur !== 'SGD' && line.value_minor !== null ? `<tr><td class="op"></td><td class="small muted">${esc(line.rate?.text || '')}</td><td class="r">${esc(money(line.value_minor, 'SGD'))}</td></tr>` : ''}
            </tbody></table>`;
    } else if (line && line.made_of) {
        tieLine = `<p>${esc(line.made_of.text)}</p><p class="num"><b>${esc(money(line.balance_minor, cur))}</b></p>`;
    }

    const tieRows = ties.ties.map(t => {
        if (t.status === 'ties' || t.status === 'off') {
            const made = t.opening_minor + t.rows_minor;
            return `<li style="margin-bottom:12px"><div class="spread"><b>Statement ${esc(day(t.date))}</b>${t.status === 'ties' ? tag('ties', 'ties') : tag('off', `off by ${money(Math.abs(t.difference_minor), cur)}`)}</div>
                <div class="small num wrap">${esc(money(t.opening_minor, cur))} on ${esc(day(t.opening_date, { year: false }))} ${t.rows_minor < 0 ? '−' : '+'} ${plural(t.rows, 'row')} ${esc(money(Math.abs(t.rows_minor), cur))} = ${esc(money(made, cur))}${t.status === 'ties' ? '' : `, but it states ${esc(money(t.closing_minor, cur))}`}</div></li>`;
        }
        if (t.status === 'your figure') return `<li style="margin-bottom:12px"><div class="spread"><b>Your figure ${esc(day(t.date))}</b>${tag('yours', 'your figure')}</div><div class="small num">${esc(money(t.closing_minor, cur))} · the fact; nothing checks it</div></li>`;
        return `<li style="margin-bottom:12px"><div class="spread"><b>Statement ${esc(day(t.date))}</b>${tag('notchecked', 'not checked: no earlier balance')}</div><div class="small num">${esc(money(t.closing_minor, cur))}</div></li>`;
    }).join('');

    const refusedHTML = refused.refused.map(f => `<div class="item refused" style="margin-top:8px"><div><div class="what">${esc(day(f.statement_date))} statement ${tag(f.set_aside ? 'aside' : 'refused', f.set_aside ? 'refused · set aside' : 'refused')}</div>
        <div class="meta">${esc(money(f.opening_minor, f.currency))} ${f.rows_minor < 0 ? '−' : '+'} ${plural(f.rows, 'row')} ${esc(money(Math.abs(f.rows_minor), f.currency))} = ${esc(money(f.opening_minor + f.rows_minor, f.currency))}, but it states ${esc(money(f.closing_minor, f.currency))}: off by ${esc(money(Math.abs(f.difference_minor), f.currency))}</div>
        <div class="holds">None of its rows is in the books.</div></div><div></div>
        <div class="act">${f.set_aside ? `<button class="btn sm" data-act="aside" data-id="${f.id}" data-aside="0">Bring it back</button>` : `<button class="btn sm" data-act="aside" data-id="${f.id}" data-aside="1">Known, leave it</button>`}<a class="btn sm" href="#/books/import">Import a fixed file</a></div></div>`).join('');

    const figures = anchorsList.filter(a => a.source === 'supplied').map(a => `<tr><td>${esc(day(a.date))}</td><td class="r num">${esc(money(a.amount_minor, cur))}</td><td class="small">${esc(a.note || '')}</td>
        <td class="r"><button class="btn sm" data-act="figure-fix" data-id="${a.id}" data-date="${a.date}" data-amount="${a.amount_minor}">Correct</button> <button class="btn sm danger" data-act="figure-delete" data-id="${a.id}">Delete</button></td></tr>`).join('');

    const rowList = rows.transactions.map(row => rowTr(row, { account: false })).join('');
    ACT.__rows = new Map(rows.transactions.map(x => [x.id, x]));
    return `${booksNav('sheet')}
    <div class="page-head"><div><a class="link small" href="#/books">← Balance sheet</a><h1>${esc(acct.name)}${accountMark(id)}</h1>
        <p>${esc(acct.type)} · ${esc(acct.owner)} · kept in ${esc(cur)}${acct.status === 'archived' ? ' · archived' : ''}</p></div>
        <div class="row">${acct.takes_a_figure ? `<button class="btn primary" data-act="figure" data-account="${id}">Enter a figure</button>` : ''}${monthPicker()}</div></div>
    <div class="grid-2">
        <section class="card"><div class="card-head"><h2>Balance</h2>${line ? `<span>${tickOf(line)} ${checkMarker(line)}</span>` : ''}</div>
            <div class="hero-figure num" style="font-size:40px">${line && line.balance_minor !== null ? heroFigure(line.balance_minor, cur) : tag('nofig', 'no figure')}</div>
            <p class="small">Rests on: ${line ? restsOn(line) : '—'}</p><hr class="rule">${tieLine}</section>
        <section class="card"><div class="card-head"><h2>Tie lines</h2><p>each statement drawn as a sum</p></div>
            ${tieRows ? `<ul style="list-style:none;padding:0;margin:0">${tieRows}</ul>` : '<p class="muted">No balances held.</p>'}
            ${refusedHTML ? `<h3 style="margin-top:12px">Refused at upload</h3>${refusedHTML}` : ''}</section>
    </div>
    ${figures ? `<section class="card"><div class="card-head"><h2>Your figures</h2></div><div class="table-wrap"><table class="t"><tbody>${figures}</tbody></table></div></section>` : ''}
    <section class="card"><div class="card-head"><h2>Rows</h2><p>${plural(rows.total, 'row')} · tap a row for its note, one-off and history</p></div>
        <div class="table-wrap"><table class="t rows"><thead><tr><th>Date</th><th>Description</th><th>Labelled</th><th class="r">Amount</th></tr></thead><tbody>${rowList || '<tr><td colspan="4" class="empty">No rows.</td></tr>'}</tbody></table></div>
        ${pager(rows, 'account-page')}</section>`;
}
ACT['account-page'] = el => { accountPage.page = Number(el.dataset.page); rerender(); };
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
ACT['figure-delete'] = async el => {
    if (!confirm('Delete this figure? You can undo it from Changes.')) return;
    const r = await act('DELETE', `/api/anchors/${el.dataset.id}`, undefined, 'Figure deleted');
    if (r) rerender();
};

// ---------------------------------------------------------------------------
// Spending: the old dashboard's views under Books, one book or every book
// with a total each, never one sum
// ---------------------------------------------------------------------------

const charts = [];
function totalsByCurrency(rows) {
    const by = {};
    rows.forEach(row => { const c = row.currency || 'SGD'; by[c] = (by[c] || 0) + toMinor(row.amount_sgd, c); });
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

async function viewSpending() {
    const sp = S.spending;
    const r = await refs();
    const months = monthsBack(S.month, 12);
    const span = sp.span === 'year' ? months : [S.month];
    const books = sp.book === 'all' ? r.books.map(b => b.name) : [sp.book];
    const qs = book => {
        const p = new URLSearchParams({ book, start: months[0] + '-01', end: monthEnd(S.month), expense_only: 'true', sort: 'date', sort_dir: 'desc' });
        if (sp.search) p.set('search', sp.search);
        if (sp.type) p.set('types', sp.type);
        if (sp.account) p.set('account_id', sp.account);
        if (!sp.oneOffs) p.set('exclude_one_off', 'true');
        return p.toString();
    };
    // Every page is read: a total is never one page's sum.
    const lists = await Promise.all(books.map(b => getAllRows(qs(b))));
    const perBook = books.map((book, i) => {
        const year = lists[i].transactions;
        const rows = year.filter(row => span.includes(row.date.slice(0, 7)));
        const others = [...new Set(year.map(row => row.currency || 'SGD').filter(c => c !== 'SGD'))];
        return { book, year, rows, by: totalsByCurrency(rows), short: shownOf(lists[i]), others };
    });
    ACT.__rows = new Map(perBook.flatMap(b => b.rows).map(x => [x.id, x]));

    const totals = `<div class="tiles">${perBook.map((b, i) => `<div class="tile ${i === 0 && b.book === r.books[0].name ? '' : 'company'}">
        <div class="eyebrow">${esc(b.book)}${i === 0 && b.book === r.books[0].name ? ' · household spending' : ' · its costs'}</div>
        <div class="fig num">${esc(totalsText(b.by))}</div><p>${plural(b.rows.length, 'row')} · ${span.length === 1 ? esc(monthName(S.month)) : `12 months to ${esc(monthName(S.month, { short: true }))}`}</p>
        ${b.short ? `<p class="small" style="color:var(--warn)">Short: too many rows to read at once (${esc(b.short)} over 12 months). Narrow the filters.</p>` : ''}</div>`).join('')}</div>
        ${perBook.length > 1 ? '<p class="never">Each book has its own total. They are never added together.</p>' : ''}`;

    const body = perBook.map(b => {
        let content;
        if (sp.view === 'flat') {
            content = `<div class="table-wrap"><table class="t rows"><thead><tr><th>Date</th><th>Description</th><th>Type</th><th class="r">Amount</th></tr></thead>
                <tbody>${b.rows.slice(0, 300).map(row => rowTr(row)).join('') || '<tr><td colspan="4" class="empty">No rows.</td></tr>'}</tbody></table></div>
                ${b.rows.length > 300 ? `<p class="small muted">The first 300 of ${b.rows.length} rows; narrow the filters to see the rest.</p>` : ''}`;
        } else {
            const groups = groupRows(b.rows, sp.view === 'type' ? row => row.display_type || 'No type' : row => row.service_name || row.description);
            content = `<div class="table-wrap"><table class="t"><thead><tr><th>${sp.view === 'type' ? 'Type' : 'Merchant'}</th><th class="r">Rows</th><th class="r">Total</th><th class="r desk-only">Share</th></tr></thead><tbody>
                ${groups.slice(0, 60).map(g => `<tr class="clickable" data-act="spend-drill" data-key="${esc(g.name)}" data-view="${sp.view}"><td>${g.name === 'No type' ? tag('nofig', 'No type') : esc(g.name)}</td><td class="r num">${g.list.length}</td>
                    <td class="r num">${esc(totalsText(g.by))}</td><td class="r num desk-only">${pct(g.sgd, b.by.SGD || 0)}</td></tr>`).join('') || '<tr><td colspan="4" class="empty">No rows.</td></tr>'}</tbody></table></div>`;
        }
        return `<section class="card"><div class="card-head"><h2>${esc(b.book)}</h2><p>total <b class="num">${esc(totalsText(b.by))}</b> · ${plural(b.rows.length, 'row')}</p></div>
            <div class="chart-box small"><canvas id="chart-${esc(b.book)}" aria-label="${esc(b.book)} by month"></canvas></div>
            <p class="small muted" style="margin:6px 0 10px">By month, 12 months to ${esc(monthName(S.month, { short: true }))}, on its own scale. Tap a month to look at it.
                ${b.others.length ? `<b>S$ rows only</b>; ${esc(b.others.map(c => SIGNS[c] || c).join(' and '))} rows are shown in the table and its total, not in the chart.` : 'In S$.'}</p>
            ${content}</section>`;
    }).join('');

    afterRender(() => drawSpendingCharts(perBook, months));
    const accounts = r.accounts.filter(a => ['bank', 'card'].includes(a.type));
    return `${booksNav('spending')}
    <div class="page-head"><div><h1>Spending</h1><p>Household spending and each company’s costs, a book at a time or every book with its own total.</p></div>${monthPicker()}</div>
    <section class="card"><div class="fields" style="gap:10px">
        <div class="row"><div class="seg" role="group" aria-label="Book">${[['all', 'All books'], ...r.books.map(b => [b.name, b.name])].map(([k, l]) =>
            `<button data-act="spend-set" data-k="book" data-v="${esc(k)}" aria-pressed="${sp.book === k}">${esc(l)}</button>`).join('')}</div>
            <div class="seg" role="group" aria-label="View">${[['flat', 'Every row'], ['merchant', 'By merchant'], ['type', 'By type']].map(([k, l]) =>
            `<button data-act="spend-set" data-k="view" data-v="${k}" aria-pressed="${sp.view === k}">${l}</button>`).join('')}</div>
            <div class="seg" role="group" aria-label="Period">${[['month', monthName(S.month, { short: true })], ['year', '12 months']].map(([k, l]) =>
            `<button data-act="spend-set" data-k="span" data-v="${k}" aria-pressed="${sp.span === k}">${esc(l)}</button>`).join('')}</div></div>
        <div class="fields two" style="grid-template-columns:repeat(auto-fit,minmax(180px,1fr))">
            <label class="field"><span>Search</span><input type="search" id="sp-search" value="${esc(sp.search)}" placeholder="Merchant, description or type"></label>
            <label class="field"><span>Type</span><select data-change="spend-type"><option value="">Every type</option><option value="__untyped__"${sp.type === '__untyped__' ? ' selected' : ''}>No type</option>
                ${r.types.filter(t => t.kind === 'spending').map(t => `<option value="${esc(t.name)}"${sp.type === t.name ? ' selected' : ''}>${esc(t.display_name || t.name)}</option>`).join('')}</select></label>
            <label class="field"><span>Account</span><select data-change="spend-account"><option value="">Every account</option>${accounts.map(a => `<option value="${a.id}"${String(a.id) === String(sp.account) ? ' selected' : ''}>${esc(a.name)}</option>`).join('')}</select></label>
            <label class="check" style="align-self:end"><input type="checkbox" data-change="spend-oneoffs"${sp.oneOffs ? ' checked' : ''}> Include one-offs</label></div>
        </div></section>
    <section class="card">${totals}</section>
    ${body}`;
}
function setSpending(k, v) { S.spending[k] = v; store.set('spending', S.spending); rerender(); }
ACT['spend-set'] = el => setSpending(el.dataset.k, el.dataset.v);
ACT['spend-type'] = el => setSpending('type', el.value);
ACT['spend-account'] = el => setSpending('account', el.value);
ACT['spend-oneoffs'] = el => setSpending('oneOffs', el.checked);
ACT['spend-drill'] = el => {
    if (el.dataset.view === 'type') { S.spending.type = el.dataset.key === 'No type' ? '__untyped__' : el.dataset.key; S.spending.search = ''; }
    else { S.spending.search = el.dataset.key; }
    S.spending.view = 'flat'; store.set('spending', S.spending); rerender();
};
document.addEventListener('keydown', e => {
    if (e.key === 'Enter' && e.target.id === 'sp-search') setSpending('search', e.target.value.trim());
});
document.addEventListener('search', e => { if (e.target.id === 'sp-search') setSpending('search', e.target.value.trim()); }, true);

function drawSpendingCharts(perBook, months) {
    charts.splice(0).forEach(c => c.destroy());
    if (typeof Chart === 'undefined') return;
    const tk = chartTokens();
    perBook.forEach((b, i) => {
        const canvas = document.getElementById(`chart-${b.book}`);
        if (!canvas) return;
        const sums = months.map(m => b.year.filter(row => row.date.startsWith(m) && (row.currency || 'SGD') === 'SGD')
            .reduce((a, row) => a + toMinor(row.amount_sgd, 'SGD'), 0) / 100);
        const fill = tk.books[i % tk.books.length];
        charts.push(new Chart(canvas, {
            type: 'bar',
            data: { labels: months.map(m => MONTHS[Number(m.slice(5)) - 1]), datasets: [{ label: `${b.book}, S$ rows only`, data: sums,
                backgroundColor: months.map(m => m === S.month ? tk.current : fill), borderRadius: 4, barPercentage: 0.64, categoryPercentage: 1 }] },
            options: {
                responsive: true, maintainAspectRatio: false, animation: false,
                plugins: { legend: { display: false }, tooltip: { callbacks: { label: c => money(Math.round(c.raw * 100), 'SGD') } } },
                scales: {
                    x: { grid: { display: false }, border: { color: tk.base }, ticks: { color: tk.ink, font: { family: tk.mono, size: 10 } } },
                    y: { border: { display: false }, grid: { color: tk.grid, lineWidth: 1 }, ticks: { color: tk.ink, font: { family: tk.mono, size: 10 }, callback: v => `S$ ${v >= 1000 ? (v / 1000) + 'k' : v}` } },
                },
                onClick: (_e, els) => { if (els.length) { S.month = months[els[0].index]; store.set('month', S.month); rerender(); } },
            },
        }));
    });
}

// ---------------------------------------------------------------------------
// Bills: the subscriptions, under Spending. A missed renewal is a queue item.
// ---------------------------------------------------------------------------

async function viewBills() {
    const [subs, r] = await Promise.all([get('/api/subscriptions'), refs()]);
    const active = subs.filter(s => s.status === 'active');
    const perBook = {};
    active.forEach(s => { const b = s.book || r.books[0].name; perBook[b] = (perBook[b] || 0) + toMinor(s.monthly_sgd, 'SGD'); });
    ACT.__subs = new Map(subs.map(s => [s.id, s]));
    const rows = subs.map(s => {
        const missed = billMissed(s);
        const cur = s.currency || 'SGD';
        return `<tr class="clickable" data-act="bill" data-id="${s.id}">
            <td><b>${esc(s.service_name || s.match_pattern)}</b><div class="small muted">${esc(s.book || '')}${s.display_type ? ' · ' + esc(s.display_type) : ''}</div></td>
            <td class="r num">${esc(money(toMinor(s.amount, cur), cur))}${s.is_variable ? '<div class="small muted">varies</div>' : ''}</td>
            <td>${esc(s.periods > 1 ? `every ${s.periods} ${s.frequency.replace('ly', '').replace('month', 'months').replace('year', 'years').replace('quarter', 'quarters')}` : s.frequency)}</td>
            <td class="r num desk-only">${esc(money(toMinor(s.monthly_sgd, 'SGD'), 'SGD'))}</td>
            <td class="desk-only small">${esc(s.account_name || '')}</td>
            <td class="num">${esc(day(s.tx_last_paid || s.last_paid))}</td>
            <td class="num">${s.status === 'active' ? esc(day(s.computed_renewal)) : '—'}</td>
            <td>${missed ? tag('stale', `missed: due ${day(missed.due, { year: false })}`, { href: '#/queue' }) : s.status === 'active' ? '<span class="small">active</span>' : tag('yours', s.status)}</td></tr>`;
    }).join('');
    return `${booksNav('bills')}
    <div class="page-head"><div><h1>Bills</h1><p>What renews and when, from the rows that pay it. A renewal with no payment seen goes to the queue.</p></div>
        <div class="row"><button class="btn" data-act="bills-refresh">Refresh from rows</button><button class="btn primary" data-act="bill" data-id="">Add a bill</button></div></div>
    <section class="card"><div class="tiles">${Object.entries(perBook).map(([b, m]) => `<div class="tile"><div class="eyebrow">${esc(b)}</div><div class="fig num">${esc(money(m, 'SGD'))}</div><p>a month, active bills</p></div>`).join('')}</div>
        <p class="never">Monthly figures in S$ at the bill's own rate; each book's bills are their own total.</p></section>
    <section class="card"><div class="table-wrap"><table class="t"><thead><tr><th>Bill</th><th class="r">Amount</th><th>How often</th><th class="r desk-only">A month</th><th class="desk-only">Paid from</th><th>Last paid</th><th>Next</th><th>State</th></tr></thead>
        <tbody>${rows || '<tr><td colspan="8" class="empty">No bills yet.</td></tr>'}</tbody></table></div></section>`;
}
ACT.bill = async el => {
    const [r, services] = await Promise.all([refs(), servicesList()]);
    const s = el.dataset.id ? ACT.__subs.get(Number(el.dataset.id)) : null;
    const sel = (id, opts, v) => `<select id="${id}">${opts.map(([k, l]) => `<option value="${esc(k)}"${String(k) === String(v ?? '') ? ' selected' : ''}>${esc(l)}</option>`).join('')}</select>`;
    openSheet(s ? esc(s.service_name || 'Bill') : 'Add a bill', `<div class="fields">
        <label class="field"><span>Merchant</span>${sel('bl-svc', [['', 'Choose'], ...services.map(x => [x.id, x.name])], s?.service_id)}</label>
        <div class="fields two"><label class="field"><span>Amount</span><input type="text" inputmode="decimal" id="bl-amount" value="${s ? esc(s.amount) : ''}"></label>
            <label class="field"><span>Currency</span>${sel('bl-cur', ['SGD', 'USD', 'INR', 'EUR', 'GBP', 'AUD'].map(c => [c, c]), s?.currency || 'SGD')}</label></div>
        <div class="fields two"><label class="field"><span>How often</span>${sel('bl-freq', [['monthly', 'monthly'], ['quarterly', 'quarterly'], ['yearly', 'yearly']], s?.frequency || 'monthly')}</label>
            <label class="field"><span>Every how many</span><input type="number" min="1" id="bl-periods" value="${s ? s.periods : 1}"></label></div>
        <label class="field"><span>Paid from</span>${sel('bl-acct', [['', '—'], ...r.accounts.filter(a => ['bank', 'card'].includes(a.type)).map(a => [a.id, a.name])], s?.account_id)}</label>
        <div class="fields two"><label class="field"><span>Renews on</span><input type="date" id="bl-renew" value="${esc(s?.renewal_date || '')}"></label>
            <label class="field"><span>State</span>${sel('bl-status', [['active', 'active'], ['paused', 'paused'], ['deactivated', 'stopped']], s?.status || 'active')}</label></div>
        <label class="field"><span>Matches rows containing</span><input type="text" id="bl-pattern" value="${esc(s?.match_pattern || '')}"></label>
        <label class="field"><span>Where to manage it</span><input type="url" id="bl-link" value="${esc(s?.link || '')}"></label>
        <label class="field"><span>Note</span><input type="text" id="bl-notes" value="${esc(s?.notes || '')}"></label></div>
        <div class="row"><button class="btn primary" data-act="bill-save" data-id="${s ? s.id : ''}">Save</button>${s ? `<button class="btn danger" data-act="bill-delete" data-id="${s.id}">Delete</button>` : ''}</div>`);
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
// ---------------------------------------------------------------------------

const listState = { search: '', filter: 'all' };
async function viewLists(tab) {
    const tabs = [['merchants', 'Merchants'], ['rules', 'Rules'], ['accounts', 'Accounts'], ['rates', 'Rates'], ['types', 'Types']];
    const head = `${booksNav('lists')}<div class="page-head"><div><h1>Lists</h1><p>What the rules and labels are made of. Every change here lands in Changes, with Undo.</p></div></div>
        <div class="chips" style="margin-bottom:16px">${tabs.map(([k, l]) => `<a class="chip" href="#/books/lists/${k}" aria-pressed="${k === tab}">${l}</a>`).join('')}</div>`;
    const search = placeholder => `<label class="field" style="max-width:360px"><span class="sr-only">Search</span><input type="search" id="ls-search" value="${esc(listState.search)}" placeholder="${placeholder}"></label>`;
    const r = await refs();
    const needle = listState.search.toLowerCase();

    if (tab === 'types') {
        const block = kind => r.types.filter(t => t.kind === kind).map(t => `<tr><td><b>${esc(t.display_name || t.name)}</b></td><td class="small">${esc(t.covers)}</td>
            <td class="small muted">${esc(t.not_for || '')}</td><td class="small">${esc(t.proposed_book || 'any book')}${t.default_one_off ? ' · one-off' : ''}</td></tr>`).join('');
        return `${head}<section class="card"><div class="card-head"><h2>Types</h2><p>One list serves every book. The list is declared in fin’s code.</p></div>
            <div class="table-wrap"><table class="t"><thead><tr><th>Type</th><th>Covers</th><th>Not for</th><th>Book</th></tr></thead><tbody>${block('spending')}</tbody></table></div></section>
            <section class="card"><div class="card-head"><h2>Income kinds</h2></div><div class="table-wrap"><table class="t"><tbody>${block('income')}</tbody></table></div></section>`;
    }
    if (tab === 'rates') {
        const saved = await get('/api/rates');
        const foreign = [...new Set(r.accounts.filter(a => a.currency && a.currency !== 'SGD').map(a => a.currency))];
        const rows = saved.slice(0, 200).map(x => `<tr><td class="num">${esc(day(x.date))}</td><td>${esc(x.pair)}</td><td class="r num">${esc(x.rate)}</td><td class="small muted">${esc(x.source || '')}</td>
            <td class="r"><button class="btn sm" data-act="rate" data-currency="${esc(String(x.pair).split('/')[0])}" data-date="${esc(x.date)}">Change</button></td></tr>`).join('');
        return `${head}<section class="card"><div class="card-head"><div><h2>Rates</h2><p>S$ for one unit, by day. A balance in another currency joins the S$ totals at the rate for its day, or the latest saved before it.</p></div>
            <div class="row">${(foreign.length ? foreign : ['INR']).map(c => `<button class="btn primary" data-act="rate" data-currency="${esc(c)}" data-date="${esc(rateDay())}">Fetch or enter a ${esc(c)} rate</button>`).join('')}</div></div>
            <div class="table-wrap"><table class="t"><thead><tr><th>Day</th><th>Pair</th><th class="r">Rate</th><th>Where from</th><th></th></tr></thead><tbody>${rows || '<tr><td colspan="5" class="empty">No rate saved yet.</td></tr>'}</tbody></table></div>
            ${saved.length > 200 ? `<p class="small muted">The newest 200 of ${saved.length}.</p>` : ''}</section>`;
    }
    if (tab === 'accounts') {
        const rows = r.accounts.map(a => `<tr><td><a href="#/books/account/${a.id}"><b>${esc(a.name)}</b></a></td><td>${esc(a.type)}</td><td>${esc(a.owner)}</td><td>${esc(a.currency)}</td>
            <td class="small">${a.anchor ? `${a.anchor.source === 'supplied' ? 'your figure' : 'statement'} ${esc(day(a.anchor.date))}: <span class="num">${esc(money(a.anchor.amount_minor, a.currency))}</span>` : tag('nofig', 'no figure')}</td>
            <td class="r">${a.takes_a_figure ? `<button class="btn sm" data-act="figure" data-account="${a.id}">Enter a figure</button> ` : ''}<button class="btn sm" data-act="account-edit" data-id="${a.id}">Edit</button></td></tr>`).join('');
        return `${head}<section class="card"><div class="card-head"><h2>Accounts</h2><button class="btn primary" data-act="account-edit" data-id="">Add an account</button></div>
            <div class="table-wrap"><table class="t"><thead><tr><th>Account</th><th>Kind</th><th>Whose</th><th>Currency</th><th>Latest balance held</th><th></th></tr></thead><tbody>${rows}</tbody></table></div></section>`;
    }
    if (tab === 'rules') {
        const rules = await get('/api/rules');
        const shown = rules.filter(x => !needle || `${x.pattern} ${x.service_name}`.toLowerCase().includes(needle));
        ACT.__rules = new Map(rules.map(x => [x.id, x]));
        const rows = shown.slice(0, 200).map(x => `<tr class="clickable" data-act="rule" data-id="${x.id}"><td class="num"><b>${esc(x.pattern)}</b></td><td class="small">${esc(x.match_type)}</td>
            <td>${esc(x.service_name || '')}</td><td class="small">${esc(x.book_override || x.book || '')}${x.type_override_id || x.type_name ? ' · ' + esc(x.type_override_id ? r.typeById.get(x.type_override_id)?.name : x.type_name) : ''}${x.book_override || x.type_override_id ? ' ' + tag('yours', 'overrides') : ''}</td>
            <td class="small num">${x.min_amount !== null || x.max_amount !== null ? `${x.min_amount ?? '…'} to ${x.max_amount ?? '…'}` : ''}</td></tr>`).join('');
        return `${head}<section class="card"><div class="card-head">${search('Search patterns or merchants')}
            <div class="row"><button class="btn" data-act="rerun-rules">Re-run every rule</button><button class="btn primary" data-act="rule" data-id="">Add a rule</button></div></div>
            <p class="small muted">${shown.length} of ${rules.length} rules${shown.length > 200 ? ' · the first 200 shown' : ''}. A rule gives a row its merchant, and with it a book and type.</p>
            <div class="table-wrap"><table class="t"><thead><tr><th>Pattern</th><th>Match</th><th>Merchant</th><th>Sets</th><th>Amount range</th></tr></thead><tbody>${rows}</tbody></table></div></section>`;
    }
    // merchants
    const services = await servicesList();
    const filters = { all: () => true, untyped: s => !s.type_id, mixed: s => s.review_each_time, hidden: s => s.exclude_from_expense_views, unused: s => !s.txn_count };
    const shown = services.filter(filters[listState.filter] || filters.all).filter(s => !needle || s.name.toLowerCase().includes(needle));
    ACT.__services = new Map(services.map(s => [s.id, s]));
    const renaming = listState.renaming;
    const nameCell = s => renaming
        ? `<td><input type="text" class="rename-input" data-rename="${s.id}" data-orig="${esc(s.name)}" value="${esc(listState.renames[s.id] ?? s.name)}" aria-label="New name for ${esc(s.name)}"></td>`
        : `<td><b>${esc(s.name)}</b></td>`;
    const rows = shown.slice(0, 200).map(s => `<tr${renaming ? '' : ` class="clickable" data-act="merchant" data-id="${s.id}"`}>${nameCell(s)}<td>${esc(s.book || '')}</td>
        <td>${s.type_id ? esc(s.display_type || s.type_name) : tag('nofig', 'no type')}</td><td class="r num">${s.txn_count}</td><td class="r num">${s.rule_count}</td>
        <td>${s.review_each_time ? tag('notchecked', 'mixed') : ''} ${s.exclude_from_expense_views ? tag('yours', 'hidden') : ''} ${s.is_one_off ? tag('yours', 'one-off') : ''}</td></tr>`).join('');
    const chip = (k, l, n) => `<button class="chip" data-act="list-filter" data-f="${k}" aria-pressed="${listState.filter === k}">${l} <span class="n">${n}</span></button>`;
    const pending = Object.keys(listState.renames).length;
    return `${head}<section class="card"><div class="card-head">${search('Search merchants')}
            <div class="row">${renaming
                ? `<button class="btn" data-act="rename-mode" data-on="0">Stop renaming</button><button class="btn primary" data-act="rename-save" id="rename-save"${pending ? '' : ' disabled'}>${pending ? `Save ${plural(pending, 'rename')}` : 'Save renames'}</button>`
                : '<button class="btn" data-act="rename-mode" data-on="1">Rename several</button>'}</div></div>
        ${renaming ? '<p class="small muted">Type the new names, then save them together: one change in Changes, with Undo. Rules and rows keep pointing at the same merchant.</p>' : ''}
        <div class="chips" style="margin-bottom:12px">${chip('all', 'All', services.length)}${chip('untyped', 'No type', services.filter(filters.untyped).length)}${chip('mixed', 'Mixed', services.filter(filters.mixed).length)}${chip('hidden', 'Hidden', services.filter(filters.hidden).length)}${chip('unused', 'No rows', services.filter(filters.unused).length)}</div>
        <div class="table-wrap"><table class="t"><thead><tr><th>Merchant</th><th>Book</th><th>Type</th><th class="r">Rows</th><th class="r">Rules</th><th></th></tr></thead><tbody>${rows || '<tr><td colspan="6" class="empty">None.</td></tr>'}</tbody></table></div>
        ${shown.length > 200 ? `<p class="small muted">The first 200 of ${shown.length}; search to narrow.</p>` : ''}</section>`;
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

ACT.merchant = async el => {
    const r = await refs();
    const s = ACT.__services.get(Number(el.dataset.id));
    const others = [...ACT.__services.values()].filter(x => x.id !== s.id);
    openSheet(esc(s.name), `<div class="fields">
        <label class="field"><span>Name</span><input type="text" id="mc-name" value="${esc(s.name)}"></label>
        <div class="fields two"><label class="field"><span>Book</span><select id="mc-book">${bookOptions(r.books, s.book, '—')}</select></label>
            <label class="field"><span>Type</span><select id="mc-type">${typeOptions(r.types, s.type_id, { blank: 'No type' })}</select></label></div>
        <label class="check"><input type="checkbox" id="mc-mixed"${s.review_each_time ? ' checked' : ''}> Mixed merchant: look at its rows each time</label>
        <label class="check"><input type="checkbox" id="mc-hidden"${s.exclude_from_expense_views ? ' checked' : ''}> Hide from spending lists (never from a sum)</label>
        <label class="check"><input type="checkbox" id="mc-oneoff"${s.is_one_off ? ' checked' : ''}> One-off</label>
        <p class="small muted">${plural(s.txn_count, 'row')} · ${plural(s.rule_count, 'rule')}${(s.rules || []).length ? ': ' + s.rules.map(x => esc(x.pattern)).join(', ') : ''}. A change of book or type is written to the rows that take their label from it.</p></div>
        <button class="btn primary block" data-act="merchant-save" data-id="${s.id}">Save</button>
        <hr class="rule"><h3>Clean up</h3>
        <div class="fields two"><label class="field"><span>Merge into</span><select id="mc-target"><option value="">Choose a merchant</option>${others.map(x => `<option value="${x.id}">${esc(x.name)}</option>`).join('')}</select></label>
            <button class="btn" style="align-self:end" data-act="merchant-merge" data-id="${s.id}">Merge</button></div>
        <button class="btn danger" data-act="merchant-delete" data-id="${s.id}">Delete this merchant</button>`);
};
ACT['merchant-save'] = async el => {
    const body = { name: $('#mc-name').value.trim(), book: $('#mc-book').value || null, type_id: $('#mc-type').value ? Number($('#mc-type').value) : null,
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
    openSheet(x ? `Rule: ${esc(x.pattern)}` : 'Add a rule', `<div class="fields">
        <div class="fields two"><label class="field"><span>Pattern</span><input type="text" id="rl-pattern" value="${esc(x?.pattern || '')}"></label>
            <label class="field"><span>Match</span><select id="rl-match">${['contains', 'startswith', 'exact'].map(m => `<option value="${m}"${(x?.match_type || 'contains') === m ? ' selected' : ''}>${m === 'startswith' ? 'starts with' : m}</option>`).join('')}</select></label></div>
        <label class="field"><span>Merchant</span><select id="rl-svc"><option value="">Choose</option>${services.map(s => `<option value="${s.id}"${s.id === x?.service_id ? ' selected' : ''}>${esc(s.name)}</option>`).join('')}</select></label>
        <div class="fields two"><label class="field"><span>Book, in place of the merchant’s</span><select id="rl-book">${bookOptions(r.books, x?.book_override, 'The merchant’s')}</select></label>
            <label class="field"><span>Type, in place of the merchant’s</span><select id="rl-type">${typeOptions(r.types, x?.type_override_id, { blank: 'The merchant’s' })}</select></label></div>
        <div class="fields two"><label class="field"><span>Only from (amount)</span><input type="text" inputmode="decimal" id="rl-min" value="${x?.min_amount ?? ''}"></label>
            <label class="field"><span>Only up to (amount)</span><input type="text" inputmode="decimal" id="rl-max" value="${x?.max_amount ?? ''}"></label></div></div>
        <div class="row"><button class="btn primary" data-act="rule-save" data-id="${x ? x.id : ''}">Save</button>${x ? `<button class="btn danger" data-act="rule-delete" data-id="${x.id}">Delete</button>` : ''}</div>
        <p class="small muted">Rows already labelled by hand keep their labels. Re-run every rule to apply a change to the rows the rules labelled.</p>`);
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
    const opt = (list, v) => list.map(x => `<option value="${esc(x.name)}"${x.name === v ? ' selected' : ''}>${esc(x.name)} · ${esc(x.description)}</option>`).join('');
    openSheet(a ? esc(a.name) : 'Add an account', `<div class="fields">
        <label class="field"><span>Name</span><input type="text" id="ac-name" value="${esc(a?.name || '')}"></label>
        ${a ? '' : `<label class="field"><span>Kind</span><select id="ac-kind">${opt(r.kinds.kinds, 'bank')}</select></label>
        <label class="field"><span>Whose</span><select id="ac-owner">${opt(r.kinds.owners, 'Household')}</select></label>
        <label class="field"><span>Currency</span><select id="ac-cur">${['SGD', 'INR', 'USD'].map(c => `<option>${c}</option>`).join('')}</select></label>`}
        <label class="field"><span>Last four digits</span><input type="text" inputmode="numeric" maxlength="4" id="ac-last4" value="${esc(a?.last_four || '')}"></label>
        ${a ? `<label class="check"><input type="checkbox" id="ac-archived"${a.status === 'archived' ? ' checked' : ''}> Archived: shown apart, still counted</label>` : ''}</div>
        <button class="btn primary block" data-act="account-save" data-id="${a ? a.id : ''}">Save</button>
        ${a && a.takes_a_figure ? `<button class="btn block" data-act="figure" data-account="${a.id}">Enter a figure</button>` : ''}`);
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

const importState = { preview: null, done: null, busy: false };
async function viewImport() {
    const [past, coverage] = await Promise.all([get('/api/import/history').catch(() => []), get('/api/statements/coverage?months=6').catch(() => null)]);
    const p = importState.preview;
    afterRender(wireDrop);
    return `${booksNav('import')}
    <div class="page-head"><div><h1>Import</h1><p>Statements go through one tie check: opening balance and the rows must come to the closing balance, exactly. One that does not tie is refused whole.</p></div></div>
    <section class="card">
        <div class="drop desk-only" id="drop">Drop statement files here, or <label class="link" for="imp-files" style="cursor:pointer">choose them</label>.</div>
        <div class="phone-only"><label class="btn primary block" for="imp-files">Choose statement files</label></div>
        <input type="file" id="imp-files" multiple hidden data-change="import-files">
        ${importState.busy ? '<p class="loading">Reading…</p>' : ''}
    </section>
    ${p ? importPreviewHTML(p) : ''}
    ${importState.done ? `<section class="card"><p class="notice">${esc(importState.done)}</p></section>` : ''}
    ${coverage ? coverageHTML(coverage) : ''}
    <section class="card"><div class="card-head"><h2>Past imports</h2></div>
        <div class="table-wrap"><table class="t"><thead><tr><th>When</th><th>Accounts</th><th class="r">Rows</th><th>State</th></tr></thead><tbody>
        ${past.map(b => `<tr><td>${esc(when(b.created_at))}</td><td class="small">${b.accounts.map(esc).join(', ')}</td><td class="r num">${b.total_lines}</td><td>${esc(b.status)}</td></tr>`).join('') || '<tr><td colspan="4" class="empty">None yet.</td></tr>'}
        </tbody></table></div></section>`;
}
/** Which statements are held: each bank and card account against the last six
 *  months, the last closed month marked as the one to have. */
function coverageHTML(c) {
    const { target_month: target, covered, total } = c.summary;
    const missing = total - covered;
    const label = m => monthName(m, { short: true }).replace(/ (\d{2})(\d{2})$/, ' ’$2');
    const head = c.months.map(m => `<th${m === target ? ' class="target"' : ''}>${esc(label(m))}</th>`).join('');
    const rows = c.accounts.map(a => `<tr><td><a href="#/books/account/${a.id}">${esc(a.short_name)}</a> <span class="small muted">${a.type === 'bank' ? 'bank' : 'card'}</span></td>${c.months.map(m => {
        const cell = c.matrix[a.id]?.[m];
        const cls = m === target ? ' target' : '';
        return cell && cell.imported
            ? `<td class="ok${cls}" title="statement held${cell.date ? ', imported ' + esc(day(cell.date)) : ''}">✓</td>`
            : `<td class="missing${cls}" title="no statement for ${esc(monthName(m))}">○</td>`;
    }).join('')}</tr>`).join('');
    return `<section class="card"><div class="card-head"><div><h2>Statements held</h2>
        <p>${missing ? `${covered} of ${total} accounts have a statement for ${esc(monthName(target))}: <b style="color:var(--warn)">${missing} missing</b>` : `All ${total} accounts have a statement for ${esc(monthName(target))}.`}</p></div></div>
        <div class="table-wrap"><table class="t coverage"><thead><tr><th>Account</th>${head}</tr></thead><tbody>${rows || `<tr><td colspan="${c.months.length + 1}" class="empty">No bank or card account yet.</td></tr>`}</tbody></table></div></section>`;
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
function tieSum(t, cur, { refused = false } = {}) {
    const made = t.opening_minor + t.rows_minor;
    return `<table class="sum small"><tbody>
        <tr><td class="op"></td><td>opening ${t.opening_date ? esc(day(t.opening_date, { year: false })) : ''}</td><td class="r">${esc(money(t.opening_minor, cur))}</td></tr>
        <tr><td class="op">${t.rows_minor < 0 ? '−' : '+'}</td><td>${plural(t.rows, 'row')}</td><td class="r">${esc(money(Math.abs(t.rows_minor), cur))}</td></tr>
        <tr class="eq"><td class="op">=</td><td>${refused ? 'what the rows make it' : 'closing'}</td><td class="r">${esc(money(made, cur))}</td></tr>
        ${refused ? `<tr><td class="op"></td><td>closing it states</td><td class="r">${esc(money(t.closing_minor, cur))}</td></tr>
            <tr class="gap"><td class="op">≠</td><td>off by</td><td class="r">${esc(money(Math.abs(t.difference_minor), cur))}</td></tr>` : ''}
        </tbody></table>`;
}
function importPreviewHTML(p) {
    const ties = p.groups.filter(g => g.tie === 'ties');
    const unchecked = p.groups.filter(g => g.tie !== 'ties' && g.total);
    const refused = p.errors.filter(e => e.tie);
    const unread = p.errors.filter(e => !e.tie);
    const rows = p.groups.reduce((a, g) => a + g.transactions.length, 0);
    const card = (cls, title, n, body) => `<section class="card result"><h3>${title}</h3><div class="big">${n}</div>${body}</section>`;
    const tiesBody = ties.map(g => g.statements.map(t => `<div style="margin-top:10px"><b>${esc(g.account)}</b> ${tag('ties', 'ties')}<div class="small muted">statement ${esc(day(t.closing_date))}</div>${tieSum(t, g.currency)}</div>`).join('')).join('') || '<p class="small muted">None.</p>';
    const refusedBody = refused.map(e => `<div style="margin-top:10px"><b>${esc(e.tie.account)}</b> ${tag('refused', 'refused')}<div class="small muted">statement ${esc(day(e.tie.closing_date))}: none of its rows will be imported</div>${tieSum(e.tie, e.tie.currency, { refused: true })}</div>`).join('') || '<p class="small muted">None.</p>';
    const uncheckedBody = unchecked.map(g => `<div style="margin-top:10px"><b>${esc(g.account)}</b> ${tag('notchecked', 'not checked')}
        <div class="small muted">it states no balance: ${plural(g.total, 'row')} come in unchecked</div>
        <table class="sum small"><tbody><tr><td class="op"></td><td>${plural(g.total, 'row')}, money out</td><td class="r">${esc(money(g.transactions.filter(t => t.amount_sgd > 0).reduce((a, t) => a + toMinor(t.amount_sgd, g.currency), 0), g.currency))}</td></tr>
        <tr><td class="op"></td><td>money in</td><td class="r">${esc(money(g.transactions.filter(t => t.amount_sgd < 0).reduce((a, t) => a - toMinor(t.amount_sgd, g.currency), 0), g.currency))}</td></tr>
        <tr class="eq"><td class="op">?</td><td>no stated balance to check against</td><td class="r"></td></tr></tbody></table></div>`).join('') || '<p class="small muted">None.</p>';
    const groupsHTML = p.groups.filter(g => g.transactions.length).map(g => `<details class="card"><summary><b>${esc(g.account)}</b> · ${plural(g.transactions.length, 'row')} · ${g.typed} with a type, ${g.untyped} without</summary>
        <div class="table-wrap" style="margin-top:10px"><table class="t rows"><thead><tr><th>Date</th><th>Description</th><th>Labelled</th><th class="r">Amount</th></tr></thead><tbody>
        ${g.transactions.map(t => `<tr><td class="num">${esc(day(t.date, { year: false }))}</td><td>${esc(t.description)}</td><td class="small">${t.type_name ? esc(t.type_name) : t.flow_type === 'review' ? tag('nofig', 'will wait for a label') : t.flow_type !== 'expense' ? esc(t.flow_type) : tag('nofig', 'no type')}</td>
            <td class="r">${rowAmount(toMinor(t.amount_sgd, g.currency), g.currency)}</td></tr>`).join('')}</tbody></table></div></details>`).join('');
    return `<div class="results">${card('ties', `${tag('ties', 'Ties')}`, ties.length, tiesBody)}${card('off', `${tag('off', 'Off by')}`, refused.length, refusedBody)}${card('notchecked', `${tag('notchecked', 'Not checked')}`, unchecked.length, uncheckedBody)}</div>
        ${unread.length ? `<section class="card"><p class="notice bad">${unread.map(e => `${esc(e.file)}: ${esc(e.error)}`).join('<br>')}</p></section>` : ''}
        ${rows ? `<section class="card"><div class="spread"><p>${plural(rows, 'row')} ready. A refused statement stays in the queue until a file that ties is imported.</p>
            <div class="row"><button class="btn" data-act="import-clear">Start again</button><button class="btn primary" data-act="import-confirm">Import ${plural(rows, 'row')}</button></div></div></section>${groupsHTML}`
            : `<section class="card"><p>Nothing to import.</p><button class="btn" data-act="import-clear">Start again</button></section>`}`;
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

function whoTag(e) {
    if (e.via === 'chat') return tag('claude', 'Claude', { title: e.actor });
    return tag('you', 'you', { title: 'in fin' });
}
function entryState(e) {
    if (e.undone_by) return `<span class="small muted">undone by #${e.undone_by}</span>`;
    if (e.blocked_by) {
        return `<div class="refusal"><b>⊘ Undo refused</b>: change #${e.blocked_by.id} (${esc(e.blocked_by.summary || 'no summary')}, ${esc(when(e.blocked_by.at))}) touched the same rows. Undo that one first.
            <button class="link" data-act="trace" data-entry="${e.id}" data-blocker="${e.blocked_by.id}">Show where</button></div>`;
    }
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

async function viewChanges() {
    const [h, settings] = await Promise.all([get('/api/history?limit=300&blockers=1', { fresh: true }), get('/api/settings', { fresh: true })]);
    if (!S.onChanges) {
        // The divider stays where the mark was when the page was opened; opening it is looking.
        S.onChanges = true;
        S.lastSeenMark = h.last_looked;
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
    const shown = entries.filter(e => entryMatches(e, mark));
    const count = fn => entries.filter(fn).length;
    let dividerDone = mark === null;
    const items = shown.map(e => {
        let divider = '';
        if (!dividerDone && e.id <= mark) {
            dividerDone = true;
            if (shown.indexOf(e) > 0) divider = `<li class="divider" role="separator">You last looked here</li>`;
        }
        const open = S.changes.open.has(e.id);
        return `${divider}<li class="entry${e.via === 'chat' ? ' claude' : ''}${e.undone_by ? ' undone' : ''}" id="entry-${e.id}">
            <div class="entry-row">
                <label class="sel"><input type="checkbox" data-entry="${e.id}" aria-label="Select change ${e.id}"${e.undone_by || e.blocked_by ? ' disabled' : ''}></label>
                <div class="when small">${esc(when(e.at))} ${whoTag(e)}</div>
                <div class="summary"><button data-act="entry-open" data-entry="${e.id}" aria-expanded="${open}">${esc(e.summary || 'A change')}
                    ${e.asked_first ? tag('asked', 'asked first, yes in chat') : ''}${e.undoes ? ` <span class="small muted">· undid #${e.undoes}</span>` : ''}</button>
                    <span class="small muted">#${e.id}${e.via === 'chat' ? ' · ' + esc(e.actor) : ''}</span></div>
                <div class="rows num small r">${plural(e.rows, 'row')}</div>
                <div class="act">${entryState(e)}</div>
            </div>
            <div class="entry-detail" id="detail-${e.id}"${open ? '' : ' hidden'}>${open ? '<p class="loading">Loading…</p>' : ''}</div></li>`;
    }).join('');
    afterRender(() => {
        S.changes.open.forEach(id => fillEntry(id));
        if (wanted) $(`#entry-${wanted}`)?.scrollIntoView({ block: 'center' });
    });

    const f = S.changes;
    const chip = (k, v, label, n) => `<button class="chip" data-act="changes-filter" data-k="${k}" data-v="${v}" aria-pressed="${String(f[k]) === String(v)}">${label}${n !== undefined ? ` <span class="n">${n}</span>` : ''}</button>`;
    const newCount = count(e => e.id > (mark || 0));
    return `<div class="page-head"><div><h1>Recent changes</h1><p>Every write, yours and Claude’s, in one undoable list. There is no confirm screen: you check here, and undo.</p></div></div>
    <section class="card">
        <div class="spread"><button class="switch" role="switch" aria-checked="${settings.claude_may_write}" data-act="claude-switch">
            <span class="track" aria-hidden="true"></span> Claude may write</button>
            <p class="small" style="flex:1 1 260px">${settings.claude_may_write
                ? 'Claude writes to fin straight from chat; each write lands here with Undo. Over ' + h.many_rows + ' rows, Claude says the count in chat and waits for your yes.'
                : 'Off: every write from chat is refused and changes nothing. Reads still work. Only you can turn it back on, here.'}</p></div>
        ${mark !== null && newCount ? `<p class="notice" style="margin-top:12px;background:var(--claude-subtle)">✳ <b>${plural(newCount, 'change')}</b> since you last looked are above the dashed line.</p>` : ''}
    </section>
    <section class="card">
        <div class="chips" style="margin-bottom:8px"><span class="small muted" style="align-self:center">Who</span>${chip('who', 'all', 'Everyone', entries.length)}${chip('who', 'claude', 'Claude', count(e => e.via === 'chat'))}${chip('who', 'you', 'You', count(e => e.via !== 'chat'))}</div>
        <div class="chips" style="margin-bottom:8px"><span class="small muted" style="align-self:center">Undo</span>${chip('state', 'any', 'Any')}${chip('state', 'can', 'Can undo', count(e => !e.undone_by && !e.blocked_by))}${chip('state', 'refused', 'Undo refused', count(e => e.blocked_by))}${chip('state', 'undone', 'Undone', count(e => e.undone_by))}${chip('state', 'undos', 'Undos', count(e => e.undoes))}</div>
        <div class="chips"><button class="chip" data-act="changes-toggle" data-k="newOnly" aria-pressed="${f.newOnly}">New since you last looked <span class="n">${newCount}</span></button>
            <button class="chip" data-act="changes-toggle" data-k="asked" aria-pressed="${f.asked}">Asked first in chat <span class="n">${count(e => e.asked_first)}</span></button></div>
        <div class="spread" style="margin-top:14px"><span class="small muted">${shown.length} of ${entries.length} changes · tap one for before → after</span>
            <button class="btn" data-act="batch-undo">Undo selected</button></div>
        <ul class="log" style="margin-top:8px">${items || '<li class="empty">No changes match.</li>'}</ul>
        <p class="small muted" style="margin-top:10px">Undo is refused when a later change touched the same rows; the refusal names that change. Undo selected runs newest first and stops at the first refusal. An undo is a change too, and can be undone.</p>
    </section>`;
}
window.addEventListener('hashchange', () => { if (!location.hash.startsWith('#/changes')) S.onChanges = false; });
ACT['changes-filter'] = el => { S.changes[el.dataset.k] = el.dataset.v; rerender(); };
ACT['changes-toggle'] = el => { S.changes[el.dataset.k] = !S.changes[el.dataset.k]; rerender(); };
ACT['claude-switch'] = async el => {
    const on = el.getAttribute('aria-checked') !== 'true';
    const r = await send('PUT', '/api/settings/claude-write', { on });
    if (!r.ok) { toast(r.data.error || 'That did not work', { bad: true }); return; }
    toast(on ? 'Claude may write again.' : 'Claude’s writes are off. Reads still work.');
    rerender();
};
ACT['entry-open'] = el => {
    const id = Number(el.dataset.entry);
    const box = $(`#detail-${id}`);
    if (S.changes.open.has(id)) { S.changes.open.delete(id); box.hidden = true; el.setAttribute('aria-expanded', 'false'); return; }
    S.changes.open.add(id); box.hidden = false; el.setAttribute('aria-expanded', 'true');
    box.innerHTML = '<p class="loading">Loading…</p>';
    fillEntry(id);
};

const SKIP_FIELDS = new Set(['id', 'created_at', 'cat_source', 'flow_type_manual', 'imported_at', 'fetched_at', 'updated_at', 'statement_id', 'printed']);
const FIELD_WORDS = { type_id: 'type', service_id: 'merchant', other_side_id: 'other side', flow_type: 'flow', is_one_off: 'one-off', notes: 'note',
    amount_minor: 'amount', account_id: 'account', type_override_id: 'type (rule)', book_override: 'book (rule)', review_each_time: 'mixed',
    exclude_from_expense_views: 'hidden', match_type: 'match', min_amount_minor: 'from', max_amount_minor: 'up to', amount: 'amount', statement_date: 'statement' };
async function shownValue(table, field, value, row, ctx, cur = 'SGD') {
    if (value === null || value === undefined || value === '') return '<span class="none">none</span>';
    if (field === 'type_id' || field === 'type_override_id') return esc(ctx.r.typeById.get(value)?.display_name || `type ${value}`);
    if (field === 'other_side_id' || field === 'account_id') return esc(ctx.r.accountById.get(value)?.name || `account ${value}`);
    if (field === 'service_id') return esc(ctx.services.find(s => s.id === value)?.name || `merchant ${value}`);
    if (['is_one_off', 'review_each_time', 'exclude_from_expense_views'].includes(field)) return value ? 'yes' : 'no';
    // Each changed row comes with its own currency (its account's): rupees
    // keep Indian grouping here as everywhere.
    if (table === 'anchors' && field === 'amount') return esc(money(value, cur));
    if (field === 'amount_minor' || field.endsWith('_amount_minor')) return esc(money(value, cur));
    if (/date$/.test(field) && typeof value === 'string') return esc(day(value));
    return esc(String(value));
}
function rowLabel(table, row, ctx) {
    if (!row) return table;
    if (table === 'transactions') return `${esc(row.description)} <span class="muted small">· ${esc(day(row.date, { year: false }))}</span>`;
    if (table === 'anchors') return `figure for ${esc(ctx.r.accountById.get(row.account_id)?.name || 'an account')} on ${esc(day(row.date))}`;
    if (table === 'services') return `merchant ${esc(row.name)}`;
    if (table === 'merchant_rules') return `rule ${esc(row.pattern)}`;
    if (table === 'accounts') return `account ${esc(row.name)}`;
    if (table === 'statements') return `statement of ${esc(ctx.r.accountById.get(row.account_id)?.name || 'an account')}, ${esc(day(row.statement_date))}`;
    if (table === 'subscriptions') return `bill ${esc(ctx.services.find(s => s.id === row.service_id)?.name || row.match_pattern || '')}`;
    if (table === 'rates') return `rate ${esc(row.pair)} ${esc(day(row.date))}`;
    return `${esc(table)} ${row.id}`;
}
async function changeRowsHTML(entry, { only = null } = {}) {
    const ctx = { r: await refs(), services: await servicesList() };
    const changes = only ? entry.changes.filter(c => c.table === only.table && c.row_id === only.row_id) : entry.changes;
    const out = [];
    for (const c of changes.slice(0, 60)) {
        const row = c.after || c.before;
        let what;
        if (c.op === 'update') {
            const fields = Object.keys(c.after).filter(k => !SKIP_FIELDS.has(k) && JSON.stringify(c.before[k]) !== JSON.stringify(c.after[k]));
            const parts = [];
            for (const k of fields) parts.push(`<span class="ba"><span class="small muted">${esc(FIELD_WORDS[k] || k.replace(/_/g, ' '))}</span> <span class="before">${await shownValue(c.table, k, c.before[k], c.before, ctx, c.currency)}</span> → <span class="after">${await shownValue(c.table, k, c.after[k], c.after, ctx, c.currency)}</span></span>`);
            what = parts.join(' &nbsp; ') || '<span class="muted small">how it is labelled (no field you see changed)</span>';
        } else if (c.op === 'insert') {
            what = `<span class="ba"><span class="after">added</span>${c.table === 'transactions' ? ` ${await shownValue(c.table, 'amount_minor', c.after.amount_minor, c.after, ctx, c.currency)}` : c.table === 'anchors' ? ` ${await shownValue(c.table, 'amount', c.after.amount, c.after, ctx, c.currency)}` : ''}</span>`;
        } else {
            what = `<span class="ba"><span class="before">removed</span></span>`;
        }
        const history = c.table === 'transactions' ? ` <button class="link small" data-act="row" data-tx="${c.row_id}">this row’s history</button>` : '';
        out.push(`<tr><td>${rowLabel(c.table, row, ctx)}${history}</td><td>${what}</td></tr>`);
    }
    const more = changes.length > 60 ? `<p class="small muted">+ ${changes.length - 60} more rows.</p>` : '';
    return `<div class="table-wrap"><table class="t"><thead><tr><th>Row</th><th>Before → after</th></tr></thead><tbody>${out.join('')}</tbody></table></div>${more}`;
}
async function fillEntry(id) {
    const box = $(`#detail-${id}`);
    if (!box) return;
    try {
        const entry = await get(`/api/history/${id}`, { fresh: true });
        box.innerHTML = `<p class="small muted" style="margin-bottom:6px">${esc(when(entry.at))} · ${entry.via === 'chat' ? `Claude (${esc(entry.actor)})` : 'you, in fin'} · ${plural(entry.rows, 'row')}${entry.asked_first ? ` · over ${S.manyRows ?? 'the'} rows: Claude said the count (${entry.asked_count}) in chat and you said yes` : ''}</p>${await changeRowsHTML(entry)}`;
    } catch (err) { box.innerHTML = `<p class="notice bad">${esc(err.message)}</p>`; }
}

async function undoEntry(id, { quiet = false } = {}) {
    const r = await send('POST', `/api/history/${id}/undo`);
    if (!r.ok) {
        if (!quiet) toast(r.data.error || 'Undo refused; nothing was changed', { bad: true, ms: 9000 });
        refreshFrame();
        if (location.hash.startsWith('#/changes')) rerender();
        return null;
    }
    if (!quiet) toast(`Undone: change #${id}`, { undo: r.data.by });
    refreshFrame();
    rerender();
    return r.data;
}
ACT.undo = el => undoEntry(Number(el.dataset.entry));
ACT['batch-undo'] = async () => {
    const ids = $$('.log input[type="checkbox"]:checked').map(x => Number(x.dataset.entry)).sort((a, b) => b - a);
    if (!ids.length) { toast('Tick the changes to undo first'); return; }
    let done = 0;
    for (const id of ids) {
        const r = await send('POST', `/api/history/${id}/undo`);
        if (!r.ok) { toast(`Undid ${done} of ${ids.length}. #${id} was refused: ${r.data.error || ''}`, { bad: true, ms: 10000 }); break; }
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

async function timelineHTML(table, rowId, { blocker = null, blocked = null } = {}) {
    const list = (await get(`/api/history/row/${table}/${rowId}`, { fresh: true })).entries;
    const details = await Promise.all(list.slice(0, 12).map(e => get(`/api/history/${e.id}`, { fresh: true })));
    const items = [];
    for (let i = 0; i < details.length; i++) {
        const e = details[i];
        const isBlocker = e.id === blocker;
        items.push(`<li class="${e.via === 'chat' ? 'claude' : ''}${isBlocker ? ' blocker' : ''}">
            <div class="small">${esc(when(e.at))} ${whoTag(e)} <span class="muted">#${e.id}</span>${e.undone_by ? ' <span class="muted">· undone</span>' : ''}</div>
            <div><b>${esc(e.summary)}</b></div>
            ${isBlocker ? `<p class="refusal">This is why change #${blocked} cannot be undone: it changed this row later. Undo #${e.id} first.</p>` : ''}
            ${await changeRowsHTML(e, { only: { table, row_id: rowId } })}
            ${e.id === blocked ? '<p class="refusal small">The change whose undo was refused.</p>' : ''}</li>`);
    }
    return items.length ? `<ul class="timeline">${items.join('')}</ul>${list.length > 12 ? `<p class="small muted">+ ${list.length - 12} older.</p>` : ''}`
        : '<p class="small muted">No change has touched it since it came in.</p>';
}
async function openTimeline(table, rowId, opts) {
    openSheet('Its own history', '<p class="loading">Loading…</p>');
    sheetBody().innerHTML = await timelineHTML(table, rowId, opts);
}

// One row: its facts, its note, the one-off toggle, "This was…", and its history.
ACT.row = el => openRowSheet(Number(el.dataset.tx));
async function openRowSheet(txId, opts = {}) {
    openSheet('A row', '<p class="loading">Loading…</p>');
    const found = (await get(`/api/transactions?tx_id=${txId}`, { fresh: true })).transactions[0];
    if (!found) { sheetBody().innerHTML = '<p class="notice">This row is no longer in the books (an import was undone, perhaps).</p>' + await timelineHTML('transactions', txId, opts); return; }
    ACT.__lastRow = found;
    const label = found.flow_type === 'review' ? tag('nofig', 'waiting for a label') : found.display_type ? esc(found.display_type) : (['expense', 'refund'].includes(found.flow_type) ? tag('nofig', 'no type') : esc(found.flow_type));
    $('#sheet-title').textContent = found.description;
    sheetBody().innerHTML = `${rowHead(found)}
        <table class="t small"><tbody>
            <tr><td class="muted">Book</td><td>${esc(found.book || '')}</td></tr>
            <tr><td class="muted">Type</td><td>${label}</td></tr>
            <tr><td class="muted">Flow</td><td>${esc(found.flow_type)}${found.other_side_name ? ' · ' + esc(found.other_side_name) : ''}</td></tr>
            <tr><td class="muted">Merchant</td><td>${esc(found.service_name || '—')}</td></tr>
            <tr><td class="muted">Labelled by</td><td>${esc({ manual: 'you or Claude, by hand', service_default: 'its merchant', rule_override: 'a rule', fallback: 'its wording', derived: 'worked out from figures', auto: 'the rules' }[found.cat_source] || found.cat_source || '')}</td></tr>
        </tbody></table>
        <div class="row"><button class="btn primary" data-act="${found.flow_type === 'review' ? 'this-was' : 'resolve'}" data-tx="${found.id}">This was…</button>
            <label class="check"><input type="checkbox" data-change="row-oneoff" data-tx="${found.id}"${found.is_one_off ? ' checked' : ''}> One-off</label></div>
        <label class="field"><span>Note</span><textarea id="row-note">${esc(found.notes || '')}</textarea></label>
        <button class="btn" data-act="row-note" data-tx="${found.id}">Save the note</button>
        <h3>Its own history</h3><div id="row-history"><p class="loading">Loading…</p></div>`;
    $('#row-history').innerHTML = await timelineHTML('transactions', txId, opts);
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
    openSheet(`${esc(currency)} rate`, `<p>A balance in ${esc(currency)} joins the S$ totals at the rate saved for its day, or the latest saved before it. With none, it is left out.</p>
        <div class="fields two"><label class="field"><span>Currency</span><select id="rt-cur">${['INR', 'USD', 'EUR', 'GBP', 'AUD'].map(c => `<option${c === currency ? ' selected' : ''}>${c}</option>`).join('')}</select></label>
            <label class="field"><span>For the day</span><input type="date" id="rt-date" value="${esc(date)}" max="${todayIso()}"></label></div>
        <button class="btn primary block" data-act="rate-fetch">Fetch the reference rate</button>
        <p class="small muted">The European Central Bank's rate for that day; a weekend or holiday takes the business day before, and the saved rate says so. A rate already saved for the day is kept.</p>
        <hr class="rule">
        <label class="field"><span>Or enter it: S$ for 1 <b id="rt-unit">${esc(currency)}</b></span><input type="text" inputmode="decimal" id="rt-rate" placeholder="for example 0.0153"></label>
        <button class="btn block" data-act="rate-enter">Save this rate for the day</button>
        <p class="small muted">An entered rate takes the place of any saved for that day. Every S$ figure is worked out on read, so the months that use it follow.</p>`);
    $('#rt-cur').addEventListener('change', () => { $('#rt-unit').textContent = $('#rt-cur').value; });
}
ACT['rate-fetch'] = async () => {
    const body = { currency: $('#rt-cur').value, date: $('#rt-date').value };
    const r = await act('POST', '/api/rates/fetch', body, 'Rate saved');
    if (r) {
        const held = r.data.rate || {};
        toast(r.data.created ? `Saved: 1 ${body.currency} = S$ ${held.rate} (${held.date})` : `Already saved for ${held.date}: 1 ${body.currency} = S$ ${held.rate}`);
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
