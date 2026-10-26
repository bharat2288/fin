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
    'check': '<path d="M229.66,77.66l-128,128a8,8,0,0,1-11.32,0l-56-56a8,8,0,0,1,11.32-11.32L96,188.69,218.34,66.34a8,8,0,0,1,11.32,11.32Z"/>',   // upstream
    'arrow-up': '<path d="M205.66,117.66a8,8,0,0,1-11.32,0L136,59.31V216a8,8,0,0,1-16,0V59.31L61.66,117.66a8,8,0,0,1-11.32-11.32l72-72a8,8,0,0,1,11.32,0l72,72A8,8,0,0,1,205.66,117.66Z"/>',   // upstream
    'arrow-down': '<path d="M205.66,149.66l-72,72a8,8,0,0,1-11.32,0l-72-72a8,8,0,0,1,11.32-11.32L120,196.69V40a8,8,0,0,1,16,0V196.69l58.34-58.35a8,8,0,0,1,11.32,11.32Z"/>',   // upstream
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
    needsLook: false,
    spending: store.get('spending', { book: 'all', view: 'type', span: 'month', search: '', type: '', oneOffs: true, account: '' }),
    changes: { who: 'all', state: 'any', newOnly: false, asked: false, open: new Set() },
    lastSeenMark: null,        // the mark as it was when Changes was opened: the divider sits there
    marks: { rows: {}, accounts: {} },  // what Claude changed since you last looked (the quiet mark)
    manyRows: null,            // the server's count over which a chat change asks first (mcp_tools.MANY_ROWS)
};

/** The one "Claude may write" switch (chat_writes.py): {enabled, changed_at,
 *  changed_by, unreadable?}. Read fresh: the chat can turn it off at any time. */
function chatWrites() { return get('/api/chat-writes', { fresh: true }); }

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
/** A row with part of it expected back or placed (personal-ledger 23), once
 *  switched: its amount struck, then what it counts, both from the payload
 *  (counted_minor), never worked out here, and the pill saying why in the
 *  server's words ("S$ 200 expected back"). The renderers call it only when
 *  counted_minor differs from the amount. */
function struckAmount(row) {
    const cur = row.currency || 'SGD';
    return `<s class="muted">${esc(money(Math.abs(row.amount_minor), cur))}</s> <span class="num">${esc(money(Math.abs(row.counted_minor), cur))}</span>${row.expect_pill ? ` ${tag('yours', row.expect_pill)}` : ''}`;
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
/** A history time (UTC, "YYYY-MM-DD HH:MM:SS", or ISO with its Z as the
 *  switch's changed_at) in the viewer's own time. */
function when(utc) {
    if (!utc) return '—';
    const t = new Date(/[zZ]|[+-]\d\d:\d\d$/.test(utc) ? utc : utc.replace(' ', 'T') + 'Z');
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
// Mid-sentence: only the first letter drops, so a name inside keeps its capitals.
function lowerFirst(s) { return s ? s.charAt(0).toLowerCase() + s.slice(1) : s; }

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
 *  it made, for the toast's Undo. Every write clears what was read, but one
 *  that changes no book data (keep: "looked") keeps it and re-reads its own. */
async function send(method, url, body, { keep = false, waitMs = 0, slow = '' } = {}) {
    const opts = { method, headers: { Accept: 'application/json' } };
    if (body instanceof FormData) opts.body = body;
    else if (body !== undefined) { opts.headers['Content-Type'] = 'application/json'; opts.body = JSON.stringify(body); }
    // waitMs: give up after so long, with `slow` as the words, rather than wait on forever.
    const stop = waitMs ? new AbortController() : null;
    const timer = stop ? (opts.signal = stop.signal, setTimeout(() => stop.abort(), waitMs)) : null;
    const gaveUp = () => ({ ok: false, status: 0, data: { error: slow } });
    let res;
    try { res = await fetch(url, opts); }
    catch (_) {
        if (timer) clearTimeout(timer);
        if (stop && stop.signal.aborted && slow) return gaveUp();
        return { ok: false, status: 0, data: { error: 'fin could not be reached; nothing was changed' } };
    }
    if (res.status === 413) { if (timer) clearTimeout(timer); return { ok: false, status: 413, data: { error: 'That is more than fin takes in one go (10 MB); nothing was changed. Send fewer files at a time.' } }; }
    const data = await res.json().catch(() => ({}));
    if (timer) clearTimeout(timer);
    if (stop && stop.signal.aborted && slow) return gaveUp();
    if (!keep) cache.clear();
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
// A page draws with the marks as last read, each in a slot of its own; the
// router's fresh read fills the slots once it arrives (applyMarks), so the
// marks never hold up a page's first draw.
function rowMark(rowId) { return `<span class="cmark-at" data-mark-row="${esc(rowId)}">${claudeMark(S.marks.rows[rowId])}</span>`; }
function accountMark(accountId) { return `<span class="cmark-at" data-mark-account="${esc(accountId)}">${claudeMark(S.marks.accounts[accountId])}</span>`; }
/** Put the marks just read into every slot drawn: a dot appears, or goes. */
function applyMarks(marks) {
    S.marks = marks;
    $$('[data-mark-row]').forEach(el => { el.innerHTML = claudeMark(marks.rows[el.dataset.markRow]); });
    $$('[data-mark-account]').forEach(el => { el.innerHTML = claudeMark(marks.accounts[el.dataset.markAccount]); });
}

async function refs() {
    const [types, books, accounts, review, kinds] = await Promise.all([
        get('/api/types'), get('/api/books'), get('/api/accounts'), get('/api/review'), get('/api/account-kinds'),
    ]);
    const typeById = new Map(types.map(t => [t.id, t]));
    const accountById = new Map(accounts.map(a => [a.id, a]));
    window.__accountById = accountById;   // for the helpers that draw an account's fix, kept by whoever read last
    return { types, books, accounts, review, kinds, typeById, accountById };
}
async function servicesList() { return get('/api/services'); }

// ---------------------------------------------------------------------------
// Toasts and sheets
// ---------------------------------------------------------------------------

/** A toast. undo: the change it offers to undo, or a list of them (undone
 *  newest first; an empty list offers none). */
function toast(message, { bad = false, undo = null, ms = 6000 } = {}) {
    if (Array.isArray(undo) && !undo.length) undo = null;
    const box = document.createElement('div');
    box.className = 'toast' + (bad ? ' bad' : ' ok');
    box.setAttribute('role', bad ? 'alert' : 'status');
    box.innerHTML = `${icon(bad ? 'warning-circle' : 'check-circle')}<span>${esc(message)}</span>`;
    if (undo) {
        const b = document.createElement('button');
        b.type = 'button';
        b.className = 'btn sm ghost';
        b.textContent = 'Undo';
        b.onclick = async () => { box.remove(); await (Array.isArray(undo) ? undoChanges(undo) : undoEntry(undo)); };
        box.appendChild(b);
    }
    $('#toasts').appendChild(box);
    setTimeout(() => box.remove(), undo ? ms + 4000 : ms);
}

let sheetCloser = null;
let pastAnswer = null;         // a question waiting on the sheet (askPast): closing it answers null
/** A sheet: a right drawer on the desk, a bottom sheet on a phone (folio's
 *  kit drawer). eyebrow sits over the title; center makes a short confirm;
 *  foot is a row of buttons kept in view under the body. */
function openSheet(title, body, { sub = '', eyebrow = '', center = false, foot = '', focusSheet = false } = {}) {
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
    // focusSheet: the sheet itself takes focus, never one of its answers (a
    // question whose answers are not preselected, as the preview's are not).
    setTimeout(() => ((focusSheet ? null : sheet.querySelector('input, select, textarea, .choice, .btn.primary')) || sheet).focus(), 30);
    return sheet;
}
function closeSheet() {
    if (sheetCloser) { sheetCloser(); sheetCloser = null; }
    $('#sheets').innerHTML = '';
    if (pastAnswer) { const answer = pastAnswer; pastAnswer = null; answer(null); }
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
        if (line.shared) return '<span class="muted">shared: enter a figure for our part</span>';
        if (line.since_label) return `<span class="muted">worked out from rows ${esc(line.since_label)}</span>`;
        return '<span class="muted">nothing to rest on yet</span>';     // the "no figure" marker sits where the amount would
    }
    const date = day(r.date, { year: !short && !r.date.startsWith(S.month.slice(0, 4)) });
    if (r.source === 'supplied') {
        const stale = r.age_days > STALE_DAYS;
        const age = `${r.age_days} days old`;
        return `${line.shared ? 'our part, ' : ''}your figure ${esc(date)}${r.age_days > 0 ? `, <b>${esc(age)}</b>` : ''} ${stale ? tag('stale', 'stale', { act: 'figure', data: { account: line.account_id }, title: 'Enter a newer figure' }) : ''}`;
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
        const [settings, q, writes] = await Promise.all([get('/api/settings', { fresh: true }), loadQueue(), chatWrites()]);
        $$('[data-count="claude"]').forEach(el => { el.textContent = settings.claude_count ? settings.claude_count : ''; el.title = `${plural(settings.claude_count, 'change')} by Claude since you last looked`; });
        $$('[data-count="queue"]').forEach(el => { el.textContent = q.count ? q.count : ''; el.title = `${plural(q.count, 'thing')} waiting`; });
        const pill = $('#write-pill');
        pill.classList.toggle('off', !writes.enabled);
        pill.title = writes.unreadable ? 'The switch could not be read, so Claude’s writes are off. Turn it on in Changes to save it again.'
            : 'Whether Claude may write to fin from chat';
        $('[data-write-state]').textContent = writes.enabled ? 'on' : 'off';
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
    [/^\/books\/merchant\/(\d+)$/, 'books', m => viewMerchant(Number(m[1]))],
    [/^\/books\/import$/, 'books', () => viewImport()],
    [/^\/changes$/, 'changes', () => viewChanges()],
];
// Expect back's page (personal-ledger 23): expect-page.js fills window.viewExpectation; until then the route draws nothing.
registerRoute(/^\/books\/expectation\/(\d+)$/, m => window.viewExpectation?.(Number(m[1])) ?? '');
/** New pages add their route here, so ROUTES stays this file's and a page exists only once its file loads. */
function registerRoute(pattern, view) { ROUTES.push([pattern, 'books', view]); }
window.registerRoute = registerRoute;

let renderToken = 0;
async function render() {
    const path = (location.hash.replace(/^#/, '') || '/').split('?')[0];
    const route = ROUTES.find(([re]) => re.test(path)) || ROUTES[0];
    const match = path.match(route[0]) || [];
    $$('[data-place]').forEach(a => { if (a.dataset.place === route[1]) a.setAttribute('aria-current', 'page'); else a.removeAttribute('aria-current'); });
    const token = ++renderToken;
    const view = $('#view');
    // The marks are read beside the page, not before it: the page draws with the
    // marks as last read and the fresh ones fill its slots when they come.
    let marksNow = null;
    get('/api/changes/marks', { fresh: true }).catch(() => null)
        .then(marks => { marksNow = marks || { rows: {}, accounts: {} }; if (token === renderToken) applyMarks(marksNow); });
    try {
        // The shared lists start with the page's own reads (the page's refs() shares them), not before them.
        const [html] = await Promise.all([route[2](match), refs()]);
        if (token !== renderToken) return;
        if (typeof html === 'string') view.innerHTML = html;
        if (marksNow) applyMarks(marksNow);   // read before this draw was put in
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

// ROUTE MEMORY begin
// Leaving a route saves its scroll and open sections for this session; Back restores them after render.
// A history entry is tagged with state once it has been reached, so a traversal (Back) arrives carrying
// the tag and a fresh link click does not: only a traversal restores, a click starts at the top.
const routeMemory = new Map();
function rememberRoute(hash) {
    routeMemory.set(hash, { y: window.scrollY, open: [...document.querySelectorAll('#view details')].map(d => d.open) });
}
function recallRoute(hash) {
    const seen = routeMemory.get(hash);
    if (!seen) { window.scrollTo(0, 0); return; }
    document.querySelectorAll('#view details').forEach((d, i) => { d.open = !!seen.open[i]; });
    window.scrollTo(0, seen.y);
}
window.addEventListener('hashchange', async e => {
    const left = String(e.oldURL || '');
    rememberRoute(left.includes('#') ? left.slice(left.indexOf('#')) : '');
    const back = !!(history.state && history.state.finBack);
    try { history.replaceState({ finBack: true }, ''); } catch { /* a tag that cannot be set only costs the restore */ }
    closeSheet();
    if (!back) window.scrollTo(0, 0);
    await render();
    if (back) recallRoute(location.hash);
});
document.addEventListener('DOMContentLoaded', () => { try { history.replaceState({ finBack: true }, ''); } catch { /* as above */ } });   // the entry the page opened on
// ROUTE MEMORY end
document.addEventListener('DOMContentLoaded', render);

// One handler for every [data-act]: the views write plain HTML.
const ACT = {};
/** The [data-act] element a click lands on, or null: a link inside an acting row (a name that opens its page) is the
 *  link's own click, so the row handler never sees it. A link that is itself the [data-act] element still acts. */
function actTarget(target) {
    const el = target.closest('[data-act]');
    if (!el) return null;
    const a = target.closest('a[href]');
    return a && a !== el && el.contains(a) ? null : el;
}
document.addEventListener('click', e => {
    const el = actTarget(e.target);
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

// New pages push [key, href, label, icon] here (#/books/types, #/books/type), so a tab exists only once its page does;
// a fifth item names the tab it goes after (Rows after Spending), else it goes last.
const booksNavExtra = [];
window.booksNavExtra = booksNavExtra;
function booksNav(current) {
    const items = [['sheet', '#/books', 'Balance sheet', 'scales'], ['spending', '#/books/spending', 'Spending', 'chart-pie-slice'],
        ['bills', '#/books/bills', 'Bills', 'receipt'], ['lists', '#/books/lists', 'Lists', 'list-bullets'], ['import', '#/books/import', 'Import', 'upload-simple']];
    for (const extra of booksNavExtra) {
        const at = items.findIndex(([k]) => k === extra[4]);
        items.splice(at < 0 ? items.length : at + 1, 0, extra);
    }
    return `<nav class="seg subnav" aria-label="Books">${items.map(([k, href, label, ic]) =>
        `<a href="${href}"${k === current ? ' aria-current="page"' : ''}>${icon(ic)}${label}</a>`).join('')}</nav>`;
}
// A link to a page, by kind. 'type' is plain text until type-page.js sets pageLinkKinds.type, so a link never lands on a stub.
const pageLinkKinds = { merchant: id => `#/books/merchant/${id}`, account: id => `#/books/account/${id}`, type: null };
window.pageLinkKinds = pageLinkKinds;
function pageLink(kind, id, text) {
    const href = pageLinkKinds[kind]?.(id);
    return href ? `<a class="link" href="${href}">${esc(text)}</a>` : esc(text);
}
window.pageLink = pageLink;
function monthPicker() {
    return `<label class="field" style="min-width:190px"><span>As at the end of</span>
        <select data-change="month">${sheetMonths().map(m => `<option value="${m}"${m === S.month ? ' selected' : ''}>${monthName(m)}${m === currentMonth() ? ' (so far)' : ''}</option>`).join('')}</select></label>`;
}
ACT.month = el => { S.month = el.value; store.set('month', S.month); rerender(); };

// ---------------------------------------------------------------------------
// What waits: one list behind Home's cards, the Queue and the counts
// ---------------------------------------------------------------------------

/** A bill the server reads as missed (bills.py holds the rule, for the app and the
 *  chat alike): its due day and the last payment seen. One waiting on its paying
 *  account's statement is not missed. */
function billMissed(sub) {
    return sub.state === 'missed' ? { due: sub.due, paid: sub.tx_last_paid || sub.last_paid } : null;
}

function rowItem(row, kind) {
    const cur = row.currency || 'SGD';
    const minor = toMinor(row.amount_sgd, cur);
    const lane = minor < 0 ? 'in' : 'out';
    // What the row has left to label: the payload's counted figure (its own
    // part once expect back is switched on), its amount until then.
    const left = row.counted_minor ?? minor;
    const base = {
        key: `${kind}-${row.id}`, kind, lane, row, currency: cur, amount: Math.abs(left), signed: left,
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
    // a merchant set to check each row: its row came in with the merchant's
    // type and usual book, and counts there until it is checked
    const label = `${row.display_type || 'no type'} · ${row.book || ''}`;
    return { ...base, holds: `Counted as ${label} until you check it`,
        actions: [{ label: `Yes, ${label}`, act: 'mixed-yes', data: { tx: row.id }, primary: true },
            { label: 'Something else', act: 'resolve', data: { tx: row.id, kind: 'mixed' } }] };
}

/** A cross-currency pair offered (fx_pair.py): the S$ row out and the row in
 *  another currency, said once in the money-out lane. */
function fxPairItem(o) {
    const sign = SIGNS[o.currency] || o.currency;
    const back = `${money(Math.abs(o.in.amount_minor), o.in.currency)} into ${o.in.account}${o.in.date !== o.out.date ? ` on ${day(o.in.date)}` : ''}`;
    return {
        key: `fxpair-${o.out.id}-${o.in.id}`, kind: 'fxpair', lane: 'out', offer: o,
        row: { id: o.out.id, description: o.out.description, date: o.out.date, account_name: o.out.account },
        currency: o.out.currency, amount: o.out.amount_minor, signed: o.out.amount_minor,
        title: o.out.description,
        meta: `${day(o.out.date)} · ${o.out.account} · ${back}`,
        holds: `${sign} ${o.per_sgd} a dollar${o.source === 'wording' ? ', the rate in its wording' : ''}; the ECB’s was ${sign} ${o.ecb_per_sgd} (${o.off_percent}% off). Pairing keeps this rate for both rows`,
        actions: [{ label: 'Yes, pair them', act: 'fx-pair-yes', data: { out: o.out.id, in: o.in.id }, primary: true },
            { label: 'Not a pair', act: 'fx-pair-no', data: { out: o.out.id, in: o.in.id } }],
    };
}
/** The offer under a pair's card: the rate, the ECB's, and the yes and no. */
function fxPairOfferHTML(card) {
    const data = a => Object.entries(a.data).map(([k, v]) => ` data-${k}="${esc(v)}"`).join('');
    return {
        html: `<div class="guess"><span>${esc(card.holds)}.</span></div>`,
        actions: card.actions.map(a => `<button type="button" class="btn sm${a.primary ? ' subtle' : ''}" data-act="${a.act}"${data(a)}>${a.primary ? icon('check-circle') : ''}${esc(a.label)}</button>`).join(''),
    };
}
ACT['fx-pair-yes'] = async el => {
    el.disabled = true;
    const r = await act('POST', '/api/fx-pairs', { out_id: Number(el.dataset.out), in_id: Number(el.dataset.in) }, 'Paired: the rate is kept for both rows');
    el.disabled = false;
    if (r) rerender();
};
ACT['fx-pair-no'] = async el => {
    el.disabled = true;
    const r = await act('POST', '/api/fx-pairs/decline', { out_id: Number(el.dataset.out), in_id: Number(el.dataset.in) }, 'Not a pair: it will not be offered again');
    el.disabled = false;
    if (r) rerender();
};

async function loadQueue() {
    if (cache.has('queue')) return cache.get('queue');
    const p = (async () => {
        const month = currentMonth();
        const [review, untyped, mixed, sheet, refused, subs, r, fx] = await Promise.all([
            getAllRows('flow=review&sort=amount&sort_dir=desc'),
            getAllRows('types=__untyped__&sort=amount&sort_dir=desc'),
            getAllRows('look=each&sort=amount&sort_dir=desc'),
            sheetFor(month),
            get('/api/statements/refused'),
            get('/api/subscriptions'),
            refs(),
            get('/api/fx-pairs/offers').catch(() => ({ offers: [] })),
        ]);
        const items = [];
        // One item per row: a row the server lists twice is shown once, as
        // the first of these it is (a leg of a cross-currency pair offered,
        // waiting for review, no type, not checked).
        const seen = new Set();
        fx.offers.forEach(o => {
            seen.add(o.out.id); seen.add(o.in.id);
            items.push(fxPairItem(o));
        });
        [[review, 'transfer'], [untyped, 'untyped'], [mixed, 'mixed']].forEach(([list, kind]) => list.transactions.forEach(row => {
            if (seen.has(row.id)) return;
            seen.add(row.id);
            items.push(rowItem(row, kind));
        }));
        const capped = [[review, 'transfers waiting'], [untyped, 'rows with no type'], [mixed, 'rows to check']]
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
            <button type="button" class="btn sm ghost" data-act="looks-right" data-upto="${esc(settings.newest)}">Looks right</button></div></div>`;
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
 *  its marker, what it holds out, the amount, and its actions (its next step
 *  outlined; accepting fin's guess is sand, a save in a row; setting aside a
 *  ghost). */
const HOME_ITEM_ICONS = { transfer: 'arrows-left-right', fxpair: 'arrows-left-right', untyped: 'warning', mixed: 'list-bullets', bill: 'receipt',
    refused: 'prohibit', off: 'prohibit', stale: 'clock', nofig: 'warning', aside: 'circle-dashed' };
function homeItemHTML(item, guesses) {
    const amount = item.kind === 'nofig' ? '' : item.kind === 'stale'
        ? `<span class="num${item.shown < 0 ? ' neg' : ''}">${esc(money(item.shown, item.currency))}</span>`
        : item.lane === 'in' || item.lane === 'out'
            ? `${dirTag(item.lane)} <span class="num">${esc(money(item.amount, item.currency))}</span>`
            : item.kind === 'refused' ? `<span class="num">off by ${esc(money(item.amount, item.currency))}</span>` : '';
    const marker = { refused: tag('refused', 'refused', { act: 'refused-sum', data: { id: item.refused?.id }, title: 'See the tie line' }),
        off: tag('off', 'off by', item.line ? { href: `#/books/account/${item.line.account_id}`, title: 'See the tie line' } : {}),
        stale: tag('stale', 'stale'), nofig: tag('nofig', 'no figure'), mixed: tag('notchecked', 'not checked'),
        bill: tag('stale', 'missed bill'), untyped: tag('nofig', 'no type') }[item.kind] || '';
    const btn = (label, cls, act, data = {}) => `<button type="button" class="btn sm${cls ? ' ' + cls : ''}" data-act="${act}"${Object.entries(data).map(([k, v]) => ` data-${k}="${esc(v)}"`).join('')}>${esc(label)}</button>`;
    const link = (label, cls, href) => `<a class="btn sm${cls ? ' ' + cls : ''}" href="${href}">${esc(label)}</a>`;
    let actions;
    let guessLine = '';
    const guess = item.row && guesses ? guesses.get(item.row.id) : null;
    if (item.kind === 'transfer') actions = btn('This was…', '', 'this-was', { tx: item.row.id });
    else if (item.kind === 'untyped' && guess) {
        guessLine = `<p class="att__guess">fin’s guess: <b>${esc(guess.name)}</b>, ${Math.round(guess.probability * 100)}% sure</p>`;
        actions = btn(`Yes, ${guess.name}`, 'subtle', 'home-guess-yes', { tx: item.row.id, type: guess.type_id, merchant: guess.merchant || '' })
            + btn('Something else', '', 'resolve', { tx: item.row.id });
    } else if (item.kind === 'untyped') actions = btn('This was…', '', 'resolve', { tx: item.row.id });
    else if (item.kind === 'mixed') actions = btn(`Yes, ${item.row.display_type || 'no type'} · ${item.row.book || ''}`, 'subtle', 'mixed-yes', { tx: item.row.id }) + btn('Something else', '', 'resolve', { tx: item.row.id, kind: 'mixed' });
    else if (item.kind === 'refused') actions = btn('See the tie line', '', 'refused-sum', { id: item.refused.id })
        + link('Import a fixed file', '', '#/books/import') + btn('Known, leave it', 'ghost', 'aside', { id: item.refused.id, aside: 1 });
    else actions = (item.actions || []).map(a => a.href ? link(a.label, '', a.href) : btn(a.label, '', a.act, a.data)).join('');
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
    const kinds = [['fxpair', 'pair across currencies', 'pairs across currencies', true], ['transfer', 'transfer', 'transfers', true], ['untyped', 'row with no type', 'rows with no type', true],
        ['mixed', 'row to check', 'rows to check', true], ['bill', 'bill not seen', 'bills not seen', false],
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

/** The guesses Home shows: `all` is loadGuesses' Map, read with the rest of Home. */
function homeGuesses(items, all) {
    const rows = items.filter(i => i.kind === 'untyped' && i.row).map(i => i.row.id);
    const out = new Map();
    rows.forEach(id => {
        const s = all.get(id);
        if (s && (s.route === 'prefill' || s.route === 'top3') && s.types.length) out.set(id, { ...s.types[0], merchant: s.merchant });
    });
    return out;
}

/** A book's twelve months as small bars, on its own scale; the tile's month dark.
 *  The months are net of refunds: one that nets below zero hangs under the axis. */
function homeBars(values, months, current, cls) {
    const top = Math.max(...values.map(v => Math.abs(v)), 0);
    const hasNeg = values.some(v => v < 0);
    const label = `Twelve months, each on this book's own scale${hasNeg ? '; a month below zero hangs under the line' : ''}`;
    return `<div class="home-bars ${cls}${hasNeg ? ' has-neg' : ''}" role="img" aria-label="${esc(label)}">${values.map((v, i) => {
        const h = top ? Math.max(2, Math.round(Math.abs(v) / top * 100)) : 2;
        const name = monthName(months[i], { short: true });
        const title = v < 0 ? `${name}, below zero: ${money(v, 'SGD')}` : name;
        return `<span class="${months[i] === current ? 'is-current' : ''}${v ? '' : ' is-zero'}${v < 0 ? ' is-neg' : ''}" style="--h:${h}%" title="${esc(title)}"></span>`;
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

/** One Home tile: the eyebrow, the label, the figure, the average line, the
 *  body, twelve small bars and the foot. The bars are the card's history. */
function homeTile(cards, months, { cls, eyebrow, label, minor, avg, body, values, href, act, book, foot }) {
    return `<a class="home-tile ${cls}" href="${href}"${act ? ` data-act="${act}" data-book="${esc(book)}" data-month="${esc(cards.ref_month)}"` : ''}>
        <span class="home-tile__eyebrow">${esc(eyebrow)}</span>
        <span class="home-tile__label">${esc(label)}</span>
        <span class="home-tile__fig num">${esc(money(minor, 'SGD'))}</span>
        ${avg}
        <span class="home-tile__body">${body}</span>
        ${values.length ? homeBars(values, months, cards.ref_month, cls) : ''}
        <span class="home-tile__foot">${esc(foot)} ${icon('caret-right')}</span></a>`;
}
/** Home's book tiles, one per declared book, from stat-cards' payload. The
 *  figure is the month net of refunds, as the rest of fin counts it; the chip
 *  names what the refunds came to. A month with no statement in is marked,
 *  not a zero. A company's tile is the part of its costs the household's own
 *  accounts paid, and says so. */
function homeBookTiles(cards, books) {
    const hist = cards.history || [];
    const months = hist.map(h => h.month);
    const tiles = [];
    books.forEach((b, i) => {
        const key = b.name.toLowerCase();
        if (!(key in cards)) return;
        const minor = toMinor(cards[key] ?? 0);
        const values = hist.map(h => toMinor(h[key] ?? 0));
        const backMinor = toMinor(cards[`${key}_back`] || 0);
        const back = backMinor ? `<span class="delta-chip delta-chip--up">${icon('arrow-circle-down')}after ${esc(money(backMinor, 'SGD'))} refunds</span>` : '';
        const missing = cards[`${key}_missing`];
        const avg = missing
            ? `<span class="tag stale">${icon('warning')}no statement for ${esc(monthName(cards.ref_month, { short: true }))}</span>`
            : homeVsAverage(minor, toMinor(cards[`avg_${key}`] ?? 0), cards.avg_months || 3);
        const go = missing ? { href: '#/books/import', foot: 'Import the statement' } : { act: 'tile-book', book: b.name, href: '#/books/spending', foot: 'See its rows' };
        const outRows = cards[`${key}_out_rows`] ?? (i === 0 ? cards.household_rows : 0) ?? 0;
        if (i === 0) {
            tiles.push(homeTile(cards, months, { cls: 'is-household', eyebrow: 'Household', label: `${b.name} spending`, minor, avg, values, ...go,
                body: `${plural(outRows, 'row')} out${back ? ` · ${back}` : ''}. Held out until labelled: out <b>${esc(money(toMinor(cards.held_out_out_total), 'SGD'))}</b> · in <b>${esc(money(toMinor(cards.held_out_in_total), 'SGD'))}</b>.` }));
        } else {
            tiles.push(homeTile(cards, months, { cls: 'is-company', eyebrow: 'Company book', label: `${b.name}, its costs`, minor, avg, values, ...go,
                body: `Costs paid from household accounts${back ? ` · ${back}` : ''}. Not household spending; it moves ${esc(b.name)}’s company balance.` }));
        }
    });
    return tiles;
}

async function viewHome() {
    const month = currentMonth();
    const prev = prevMonth(month);
    // Jev's guesses are read with the Queue, not after it: one round.
    const [settings, now, before, q, cards, r, allGuesses] = await Promise.all([
        get('/api/settings'), sheetFor(month),
        prev >= SHEET_START ? sheetFor(prev).catch(() => null) : Promise.resolve(null),
        loadQueue(), get(`/api/dashboard/stat-cards?ref_month=${prev}&history=12`), refs(), loadGuesses(),
    ]);
    const shown = { out: q.lanes.out.slice(0, 3), in: q.lanes.in.slice(0, 3), books: q.lanes.books.slice(0, 3) };
    const guesses = homeGuesses([...shown.out, ...shown.in], allGuesses);

    const lane = (key, title, list) => `<div class="home-lane home-lane--${key}">
        <div class="home-lane__head"><h3>${key === 'out' || key === 'in' ? dirTag(key) : ''}${title}</h3>
            <p class="home-lane__sum">${list.length ? laneSummary(list) : ''}</p></div>
        ${list.length ? shown[key].map(i => homeItemHTML(i, guesses)).join('') : `<p class="card-empty">${icon('check-circle')}Nothing waits here.</p>`}
        ${list.length <= 3 ? '' : key === 'books'   // figures and statements wait in Books, not the Queue
            ? `<button type="button" class="link home-lane__more" data-act="needs-look-link">${list.length - 3} more in Books</button>`
            : `<a class="link home-lane__more" href="#/queue">${list.length - 3} more in the queue</a>`}</div>`;
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
    const tiles = homeBookTiles(cards, r.books);
    const loanValues = hist.map(h => toMinor(h.loan_principal || 0));
    tiles.push(homeTile(cards, months, { cls: 'is-movement', eyebrow: 'Movement, not spending', label: 'Loan principal repaid', minor: toMinor(cards.loan_principal), values: loanValues, href: '#/books', foot: 'See the loans',
        avg: toMinor(cards.loan_instalments) ? '' : '<span class="home-vs">no instalment seen this month</span>',
        body: `What the loans fell by: instalments ${esc(money(toMinor(cards.loan_instalments), 'SGD'))} less interest ${esc(money(toMinor(cards.loan_interest), 'SGD'))}. Still the household’s money.` }));
    const tilesCard = `<section class="card home-tiles">
        <div class="section-header"><h2>${esc(monthName(cards.ref_month))}: ${tiles.length} figures, never added</h2><span class="row"><a class="link" href="#/books/rows?month=${esc(cards.ref_month)}">Every row</a><a class="link" href="#/books/spending">Spending</a></span></div>
        <div class="home-tiles__grid">${tiles.join('')}</div>${cards.unvalued_count ? `<div class="notice warn">${icon('warning')}<span class="grow">${plural(cards.unvalued_count, 'row')} in another currency ${cards.unvalued_count === 1 ? 'has' : 'have'} no saved rate for ${esc(monthName(cards.ref_month))} and ${cards.unvalued_count === 1 ? 'is' : 'are'} not in these figures.</span><button class="btn" data-act="fetch-missing-rates">Fetch the rates</button></div>` : ''}
        <p class="card-foot">Each book is its own figure, drawn three different ways so they never read as one sum. Each small chart has its own scale. No total is shown between them.</p></section>`;

    const mcCard = before ? monthCheckMini(before) : '';
    return `<div class="home">${homeClaudeLine(settings)}${homeHero(now, before, prev)}${mcCard}${waits}${tilesCard}</div>`;
}

// The ECB rate for every day a row in another currency has none (valuation.py).
ACT['fetch-missing-rates'] = async el => {
    el.disabled = true;
    const r = await send('POST', '/api/rates/fetch-missing');
    if (!r.ok) { el.disabled = false; toast('The rates could not be fetched; nothing was saved', { bad: true }); return; }
    const failed = (r.data.failed || []).map(c => SIGNS[c] || c);
    toast([r.data.saved ? `${plural(r.data.saved, 'rate')} saved` : 'No rate was missing',
        failed.length ? `${failed.join(' and ')} could not be fetched` : ''].filter(Boolean).join('; '), { bad: failed.length > 0 });
    rerender();
};
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
    const body = { tx_id: row.id, service_name: el.dataset.merchant || row.service_name || suggestName(row.description) || titleCase(pattern || row.description),
        type_id: Number(el.dataset.type), apply_scope: pattern ? 'service_default' : 'transaction', pattern, match_type: 'contains', suggestion_visible: true };
    const r = await sendPreviewed('POST', '/api/transactions/resolve', body, { key: 'past_rows', taught: body.tx_id });
    if (r.cancelled) return;
    if (!r.ok) { toast(r.data.error || 'That did not work; nothing was changed', { bad: true }); return; }
    toast(resolvedWords(r, 'Labelled'), { undo: r.change });
    refreshFrame();
    rerender();
};

ACT['looks-right'] = async el => {
    const r = await send('POST', '/api/changes/looked', { upto: Number(el.dataset.upto) }, { keep: true });
    if (!r.ok) { toast(r.data.error || 'That did not work', { bad: true }); return; }
    toast('Marked as looked at. Nothing was undone.');
    await refreshFrame();   // the Claude count, read fresh, before Home draws it again
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
// kit (from folio): bandedTable, rowDetailToggle and bindRowDetail, ported
// by shape (fin has no modules); their CSS is the "kit (from folio)" blocks
// ---------------------------------------------------------------------------

/** kit (from folio): bandedTable. The tight table with a header band: a
 *  caption a screen reader reads, th scope="col", money right in tabular
 *  figures. columns: [{label, cls, sr}] (sr: the label is for a screen
 *  reader only); rows: each row's <tr> HTML; colgroup: a width in px for each
 *  column, 0 for the one that takes the rest. */
function bandedTable({ columns, rows, caption, colgroup = null, cls = '' }) {
    const cols = colgroup ? `<colgroup>${colgroup.map(w => w ? `<col style="width:${w}px">` : '<col>').join('')}</colgroup>` : '';
    const head = columns.map(c => `<th scope="col"${c.cls ? ` class="${c.cls}"` : ''}>${c.sr ? `<span class="sr-only">${esc(c.label)}</span>` : esc(c.label)}</th>`).join('');
    return `<table class="bt${cls ? ' ' + cls : ''}"><caption class="sr-only">${esc(caption)}</caption>${cols}<thead><tr>${head}</tr></thead><tbody>${rows.join('')}</tbody></table>`;
}
/** kit (from folio): rowDetailToggle, the caret inside the name: a button
 *  that opens the detail it controls (id). act: the ACT that toggles it. */
function rowDetailToggle({ id, label, open = false, act, data = {} }) {
    const attrs = Object.entries(data).map(([k, v]) => ` data-${k}="${esc(v)}"`).join('');
    return `<button type="button" class="disc" data-act="${act}"${attrs} aria-expanded="${open}" aria-controls="${esc(id)}">${icon('caret-right', 'caret')}<span class="nm">${esc(label)}</span></button>`;
}
/** kit (from folio): bindRowDetail, as fin's one listener calls it: the
 *  toggle shows or hides what it controls and says so. Returns open. */
function toggleRowDetail(el) {
    const open = el.getAttribute('aria-expanded') !== 'true';
    const box = document.getElementById(el.getAttribute('aria-controls'));
    if (box) box.hidden = !open;
    el.setAttribute('aria-expanded', String(open));
    return open;
}

// ---------------------------------------------------------------------------
// Sorting a table by its columns: the balance sheet's heading, for every list.
// A table names its sortable columns once, as {key: {label, kind, of}}: kind
// is text, money (its S$ figure, in minor units), date or number; of reads a
// row's value. A heading cycles ↕ → ↑ → ↓ → the table's own order; a table
// that stacks on a phone gets a Sort select above it. A table the server pages
// sends the key as its sort (sortServer): the server's allowlist reaches SQL.
// ---------------------------------------------------------------------------

/** Each table's sort, by the table's name: kept while the page is open, gone on reload. */
const SORTS = {};
/** A table that redraws itself on a new sort (a page of rows, a change opened); the rest rerender. */
const SORT_REDRAW = {};
function sortOf(table) { return SORTS[table] || { key: null, dir: 'asc' }; }
/** The next sort after a heading click: ↑ first, then ↓, then the table's own order. */
function nextSort(now, key) {
    if (now.key !== key) return { key, dir: 'asc' };
    return now.dir === 'asc' ? { key, dir: 'desc' } : { key: null, dir: 'asc' };
}
/** The value a cell sorts by, or null when it is empty. */
function sortValue(kind, v) {
    if (v === null || v === undefined || v === '') return null;
    if (kind === 'text') return String(v).trim().toLowerCase() || null;
    const n = kind === 'date' ? Date.parse(v) : Number(v);
    return Number.isNaN(n) ? null : n;
}
/** Two values of one kind in a direction: text A to Z ignoring case; empties last both ways. */
function sortCompare(kind, a, b, dir = 'asc') {
    const x = sortValue(kind, a), y = sortValue(kind, b);
    if (x === null || y === null) return x === y ? 0 : x === null ? 1 : -1;
    const d = kind === 'text' ? x.localeCompare(y, undefined, { numeric: true }) : x < y ? -1 : x > y ? 1 : 0;
    return dir === 'desc' ? -d : d;
}
/** The rows in the table's sort, or as they came when it has none. */
function sortRows(table, rows, cols) {
    const s = sortOf(table), col = cols[s.key];
    return col ? [...rows].sort((a, b) => sortCompare(col.kind, col.of(a), col.of(b), s.dir)) : rows;
}
/** A cell drawn as HTML (a marker, a tag), as the words it sorts by. */
const plainText = html => String(html || '').replace(/<[^>]*>/g, ' ').replace(/\s+/g, ' ').trim();
/** A server-paged table's order as the server's sort and sort_dir, or its own order. */
function sortServer(table, cols, own = { sort: 'date', sort_dir: 'desc' }) {
    const s = sortOf(table), col = cols[s.key];
    if (!col) return own;
    return { sort: col.server || s.key, sort_dir: s.dir === 'desc' ? 'desc' : 'asc' };
}
/** A heading that sorts its table; the arrow beside it is the stylesheet's. */
function sortHead(table, key, cols) {
    const s = sortOf(table);
    return `<button type="button" data-act="sort" data-table="${table}" data-key="${key}"${s.key === key ? ` data-dir="${s.dir}"` : ''}>${esc(cols[key].label)}</button>`;
}
const SORT_WORDS = { text: ['A to Z', 'Z to A'], money: ['smallest first', 'largest first'], number: ['fewest first', 'most first'], date: ['oldest first', 'newest first'] };
/** The phone's Sort select, for a table that stacks into rows: each column both ways, or the
 *  table's own choices ([value, words]; a bare key is that column ↑). */
function sortPicker(table, cols, { own = 'As listed', cls = 'sort-pick', choices = null } = {}) {
    const s = sortOf(table);
    const list = choices || Object.entries(cols).flatMap(([k, c]) => SORT_WORDS[c.kind].map((w, i) => [`${k}:${i ? 'desc' : 'asc'}`, `${c.label}: ${w}`]));
    const on = v => { const [k, d = 'asc'] = v.split(':'); return k === s.key && d === s.dir; };
    return `<label class="field phone-only ${cls}">${cls === 'sort-pick' ? '<span>Sort</span>' : ''}<select data-change="sort-pick" data-table="${table}" aria-label="Sort">
        <option value="">${esc(own)}</option>${list.map(([v, w]) => `<option value="${v}"${on(v) ? ' selected' : ''}>${esc(w)}</option>`).join('')}</select></label>`;
}
function setSort(table, s) { SORTS[table] = s; (SORT_REDRAW[table] || rerender)(); }
ACT.sort = el => setSort(el.dataset.table, nextSort(sortOf(el.dataset.table), el.dataset.key));
ACT['sort-pick'] = el => { const [key, dir = 'asc'] = el.value.split(':'); setSort(el.dataset.table, key ? { key, dir: dir === 'desc' ? 'desc' : 'asc' } : { key: null, dir: 'asc' }); };

// ---------------------------------------------------------------------------
// Queue
// ---------------------------------------------------------------------------

// The Queue's cards: rows with no type from one merchant share a card (Q1.3),
// and each lane holds only what needs thought, in working order (fin-surfaces
// 06): a step is a section that folds, with its count.
const queueOpen = {};
const QUEUE_PAGE = 6;
const LINES_PAGE = 12;         // the sure card's merchants, and a Check each row card's lines, shown before "Show N more"
const queueGroups = new Map();     // a card's key -> its rows, for "This was… (all 4)"
const transferGuesses = new Map(); // a waiting transfer's id -> fin's guess for it
const QUEUE_STEPS = [
    { k: 'fxpair', label: 'Pair across currencies?' },
    { k: 'transfer', label: 'Transfer?' },
    { k: 'pick', label: 'Pick a type' },
    { k: 'noguess', label: 'No guess' },
    { k: 'mixed', label: 'Check each row' },   // a merchant set to check each row: its rows not checked yet
    { k: 'bill', label: 'Missed bill' },
];

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

/** Every row with no type, grouped by merchant, with the stored guess for
 *  each group: one read for the Queue and Home instead of one per row.
 *  Returns a Map tx id → {merchant, route, types}; an empty Map when it
 *  cannot be read (no guesses, and every card still says "This was…"). */
async function loadGuesses() {
    if (cache.has('guesses')) return cache.get('guesses');
    const p = get('/api/queue/guesses').then(data => {
        const byRow = new Map();
        data.groups.forEach(({ merchant, route, types, tx_ids }) => tx_ids.forEach(id => byRow.set(id, { merchant, route, types })));
        return byRow;
    });
    cache.set('guesses', p);
    p.catch(() => cache.delete('guesses'));
    return p.catch(() => new Map());
}

/** fin's stored type guess for a row with no type, as the card and the step
 *  show it. The server hands over the route; this only draws it. A card
 *  passes its guess from loadGuesses (null: none); the step reads its row's
 *  own. Returns {html, actions, top3, visible}. */
async function showResolveSuggestion(row, { n = 1, where = 'card', guess } = {}) {
    const none = { html: '', actions: '', top3: '', visible: false };
    let suggestion = guess;
    if (suggestion === undefined) {
        try { suggestion = await get(`/api/transactions/${row.id}/suggestion`); } catch (_) { return none; }
    }
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
            actions: yes(top, 'sm subtle'), top3: '', visible: true,
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

// ---------------------------------------------------------------------------
// Type suggestion: prepare, read and tick the names, send only the ticked
// (ADR-005). Offered only while the server says it is on (it is off without
// its key); nothing leaves fin until the operator has read the list.
// ---------------------------------------------------------------------------

async function suggestionsOn() {
    try { return !!(await get('/api/suggestions/status')).enabled; } catch (_) { return false; }
}
function suggestEntryHTML({ lists = false } = {}) {
    return `<p class="suggest-entry${lists ? ' suggest-entry--lists' : ''}"><span class="small muted">${lists ? 'Merchants no rule knows can be' : 'These can be'} sent for a type suggestion, one name at a time, after you read the list.</span>
        <button type="button" class="btn sm ghost" data-act="suggest-types">${icon('list-bullets')}Suggest types…</button></p>`;
}
let suggestList = [];
ACT['suggest-types'] = async el => {
    el.disabled = true;
    const r = await send('POST', '/api/suggestions/prepare');
    el.disabled = false;
    if (!r.ok) { toast(r.data.error || 'The list could not be prepared; nothing was sent.', { bad: true }); return; }
    suggestList = r.data.merchants || [];
    if (!suggestList.length) { toast('No new merchant names to ask about. Nothing was sent.'); return; }
    const items = suggestList.map((m, i) => `<label class="check suggest-pick"><input type="checkbox" class="suggest-pick-box" data-index="${i}" checked data-change="suggest-tick"><span>${esc(m)}</span></label>`).join('');
    openSheet('Read the list before anything is sent', `<p>Nothing has been sent. Each ticked name below is sent on its own, with the type list and nothing else. Untick any that is a person’s name or that you do not want sent.</p>
        <div class="suggest-pick-head"><span class="small muted" id="suggest-n">${plural(suggestList.length, 'name')} ticked</span>
            <span><button type="button" class="btn sm ghost" data-act="suggest-all" data-on="1">Tick all</button><button type="button" class="btn sm ghost" data-act="suggest-all" data-on="0">Untick all</button></span></div>
        <div class="suggest-pick-list" role="group" aria-label="Merchant names to send">${items}</div>`,
        { eyebrow: 'Suggest types', center: true,
          foot: `<button class="btn" data-act="close-sheet">Not now</button><button class="btn primary" data-act="suggest-send" id="suggest-send">Send the ticked names</button>` });
};
function suggestTicked() {
    return $$('.suggest-pick-box').filter(b => b.checked).map(b => suggestList[Number(b.dataset.index)]);
}
ACT['suggest-tick'] = () => {
    const n = suggestTicked().length;
    const label = $('#suggest-n'); if (label) label.textContent = `${plural(n, 'name')} ticked`;
    const go = $('#suggest-send'); if (go) go.disabled = n === 0;
};
ACT['suggest-all'] = el => { $$('.suggest-pick-box').forEach(b => { b.checked = el.dataset.on === '1'; }); ACT['suggest-tick'](); };
ACT['suggest-send'] = async el => {
    const ticked = suggestTicked();
    if (!ticked.length) { toast('Nothing ticked, so nothing was sent.'); return; }
    el.disabled = true;
    const r = await send('POST', '/api/suggestions/send', { merchants: ticked });
    closeSheet();
    if (!r.ok) { toast(r.data.error || 'Nothing was sent.', { bad: true }); return; }
    const parts = [`${plural(r.data.sent, 'merchant name')} answered`];
    if (r.data.stopped) parts.push(`Stopped: ${r.data.stopped}. ${r.data.remaining} not sent`);
    toast(parts.join('. ') + '.', { bad: !!r.data.stopped, ms: 9000 });
    rerender();
};

/** fin's guess for a waiting transfer, as words and the "Yes" it offers. */
function transferGuessWords(g) {
    const other = g.other_side ? esc(g.other_side) : 'that account';
    const words = {
        spending: [`<b>spending</b>, ${esc(g.type || '')}${g.book ? `, ${esc(g.book)}` : ''}`, `Yes, ${g.type || 'spending'}`],
        gift: ['<b>a gift</b>', 'Yes, a gift'],
        own_account: [`<b>a move to my own account</b>, ${other}`, `Yes, a move to ${g.other_side || 'it'}`],
        company: [`<b>money into a company</b>, ${other}`, `Yes, into ${g.other_side || 'the company'}`],
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
        actions: yes('sm subtle'),
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

/** Several rows' first and last dates: "12 Sep to 9 Oct 2026", the year
 *  said once when they share it. */
function spanWords(rows) {
    const dates = rows.map(x => x.date).sort();
    const a = dates[0], b = dates[dates.length - 1];
    return a === b ? day(a) : `${day(a, { year: a.slice(0, 4) !== b.slice(0, 4) })} to ${day(b)}`;
}
/** Several rows' dates and accounts, in one line: "3 Mar to 9 Apr 2026 · DBS". */
function rowsMeta(rows) {
    const accounts = new Set(rows.map(x => x.account_name));
    return `${spanWords(rows)} · ${accounts.size === 1 ? rows[0].account_name : plural(accounts.size, 'account')}`;
}
/** Rows as a banded sub-table with its header and caption (fin-surfaces 17,
 *  T7): date, account, statement text, amount; book(x), when given, adds
 *  each row's Book cell. */
function rowsTableHTML(rows, caption, { book = null } = {}) {
    const columns = [{ label: 'Date' }, { label: 'Account' }, { label: 'Statement text' }, { label: 'Amount', cls: 'r' }];
    if (book) columns.push({ label: 'Book' });
    return `<div class="tshell">${bandedTable({ columns, caption, cls: 'bt--in', rows: rows.map(x => `<tr data-tx="${x.id}"><td class="num">${esc(day(x.date))}</td>
        <td>${esc(x.account_name || '')}</td><td class="stext">${esc(x.description || '')}</td>
        <td class="r num">${esc(money(Math.abs(toMinor(x.amount_sgd, x.currency)), x.currency))}</td>${book ? book(x) : ''}</tr>`) })}</div>`;
}
/** Money out or money in, once for a side: folio's kind-pill square, no word. */
function dirSquare(lane) {
    return `<span class="dir ${lane}" aria-hidden="true"><span class="dir__icon">${icon(lane === 'in' ? 'arrow-down' : 'arrow-up')}</span></span>`;
}

// A lane row (fin-surfaces 17): what | amount | action, on the side's one
// grid, so amounts and actions line up down the side. Several rows open on
// "2 rows" at the start of the meta line to their sub-table.
const qiShown = new Set();     // the keys of the lane rows opened to their rows
function qiMeta(card, words) {
    if (card.rows.length < 2) return esc(words || '');
    const open = qiShown.has(card.key);
    return `<button type="button" class="disc disc--meta" data-act="qi-rows" data-key="${esc(card.key)}" aria-expanded="${open}" aria-controls="qr-${esc(card.key)}">${plural(card.rows.length, 'row')}${icon('caret-right', 'caret')}</button> · ${esc(rowsMeta(card.rows))}`;
}
function qiRowsHTML(card) {
    if (card.rows.length < 2) return '';
    return `<div class="qi__more" id="qr-${esc(card.key)}"${qiShown.has(card.key) ? '' : ' hidden'}>${rowsTableHTML(card.rows, `${card.merchant || card.title}: its rows`)}</div>`;
}
ACT['qi-rows'] = el => { if (toggleRowDetail(el)) qiShown.add(el.dataset.key); else qiShown.delete(el.dataset.key); };

/** One row in a money lane: a transfer, a merchant's rows with no type and
 *  no guess, or a missed bill (Pick a type and Check each row draw their own).
 *  One outline action; a missed bill's Open Bills is a link on its meta. */
/** A card's title; an untyped card whose row has a merchant links it to that merchant's page. */
function queueTitleHTML(card, title = card.title) {
    const svc = card.kind === 'untyped' && card.rows[0]?.service_id;
    return svc ? pageLink('merchant', svc, title) : esc(title);
}
async function queueCardHTML(card) {
    const n = card.rows.length;
    if (n) queueGroups.set(card.key, card.rows);
    const row = card.rows[0];
    let guess = { html: '', actions: '' };
    if (card.kind === 'untyped') guess = await showResolveSuggestion(row, { n, guess: (await loadGuesses()).get(row.id) || null });
    else if (card.kind === 'transfer') guess = transferGuessHTML(row, await transferGuess(row));
    else if (card.kind === 'fxpair') guess = fxPairOfferHTML(card);
    const bill = card.kind === 'bill';
    const title = bill ? card.sub.service_name || card.sub.match_pattern : card.title;
    const meta = bill ? `No payment seen · ${card.meta}`
        : card.kind === 'transfer' && card.holds ? `${card.meta} · ${card.holds.charAt(0).toLowerCase()}${card.holds.slice(1)}` : card.meta;
    const data = a => Object.entries(a.data || {}).map(([k, v]) => ` data-${k}="${esc(v)}"`).join('') + (card.rows.length ? ` data-group="${esc(card.key)}"` : '');
    const own = (card.actions || []).find(a => !a.href);
    const links = (card.actions || []).filter(a => a.href).map(a => ` · <a class="link" href="${a.href}">${esc(a.label)}</a>`).join('');
    // A pair offered says its yes and its no under the offer, as a guess does.
    const act = card.kind === 'fxpair' ? ''
        : own ? `<button type="button" class="btn sm" data-act="${own.act}"${data(own)}>${esc(guess.actions && /^This was/.test(own.label) ? 'Something else' : own.label)}</button>` : '';
    return `<article class="qi qi--${card.kind}">
        <div class="qi__w"><p class="qi__t">${queueTitleHTML(card, title)}${row ? rowMark(row.id) : ''}</p><p class="qi__m">${qiMeta(card, meta)}${links}</p></div>
        <div class="qi__amt num">${esc(sumByCurrency([card], c => c.amount))}</div>
        <div class="qi__act">${act}</div>
        ${guess.html ? `<div class="qi__more q-guess">${guess.html}${guess.actions ? `<div class="q-guess__act">${guess.actions}</div>` : ''}</div>` : ''}
        ${qiRowsHTML(card)}</article>`;
}

/** A lane's items as the cards it draws, each under its step: a waiting
 *  transfer; Jev's three guesses for a merchant (one card per merchant
 *  string, as the guesses read groups them); rows with no guess (one card
 *  per merchant text, as before); a Check each row merchant's rows not
 *  checked yet (one card per merchant) and a missed bill. `find` is the merchant text the search reads. */
function laneCards(lane, items, guesses) {
    const cards = [];
    const picks = new Map(), mixed = new Map(), noGuess = [];
    const group = (map, name, make, i) => {
        let card = map.get(name);
        if (!card) { card = { ...make, lane, merchant: name, title: name, find: name, items: [], rows: [] }; map.set(name, card); cards.push(card); }
        card.items.push(i);
        card.rows.push(i.row);
    };
    items.forEach(i => {
        if (i.kind === 'transfer') cards.push({ ...i, step: 'transfer', rows: [i.row], find: i.title });
        else if (i.kind === 'fxpair') cards.push({ ...i, step: 'fxpair', rows: [i.row], find: `${i.title} ${i.offer.in.description || ''}` });
        else if (i.kind === 'bill') cards.push({ ...i, step: 'bill', rows: [], find: i.sub.service_name || i.sub.match_pattern || '' });
        else if (i.kind === 'mixed') group(mixed, i.row.service_name || i.row.description || '', { key: `each-${i.row.id}`, kind: 'mixed', step: 'mixed' }, i);
        else if (i.kind === 'untyped') {
            const g = guesses.get(i.row.id);
            if (g && g.route === 'top3' && g.types && g.types.length) group(picks, g.merchant, { key: `pick-${i.row.id}`, kind: 'untyped', step: 'pick', guess: g }, i);
            else noGuess.push(i);
        }
    });
    merchantCards(noGuess).forEach(c => cards.push({ ...c, step: 'noguess', find: c.title }));
    return cards;
}
/** A card the lanes drew, found by its key (drawn in place, so focus stays). */
function laneCardEl(key) { return $$('#q-work article[data-key]').find(a => a.dataset.key === key); }
/** Every row the same | Check each row, as a segmented control: act is the
 *  ACT its press calls (data-v is the choice). */
function eachSegHTMLQ(act, key, name, each, { labelledBy = '' } = {}) {
    const label = labelledBy ? ` aria-labelledby="${labelledBy}"` : ` aria-label="How ${esc(name)}’s new rows arrive"`;
    return `<div class="seg each-seg" role="group"${label}>${[['same', 'Every row the same'], ['each', 'Check each row']].map(([v, words]) =>
        `<button type="button" data-act="${act}" data-key="${esc(key)}" data-v="${v}" aria-pressed="${(v === 'each') === !!each}"${queueBusy ? ' disabled' : ''}>${words}</button>`).join('')}</div>`;
}

// Pick a type: Jev's three guesses as pick buttons, none chosen; a pick shows
// a book picker prefilled with the type's book, and Save is the sure card's save.
const pickState = new Map();   // a card's key -> {typeId, book, each} once a type is picked
let queueBusy = false;         // a Queue save is running: every other save waits for it

/** A save that would start while another runs is refused, and says so. */
function queueSaveWaits() {
    if (queueBusy) toast('One save at a time: wait for the one running to finish.');
    return queueBusy;
}
/** A Queue save starts: every save on the sure card and the lanes waits, and
 *  so does everything on the lane card being saved (box). */
function queueSaving(box = null) {
    queueBusy = true;
    drawSureState();
    $$('#q-work [data-act="pick-save"], #q-work [data-act="each-save"], #q-work [data-act="each-ok"]').forEach(b => { b.disabled = true; });
    if (box) $$('button, select', box).forEach(b => { b.disabled = true; });
}
/** Who each row on the Queue is, by the merchant its card names: for a toast
 *  that names rows a save reached beyond its own. */
function queueNames() {
    const names = new Map();
    if (!queueModel) return names;
    queueModel.sure.forEach(g => g.items.forEach(i => names.set(i.row.id, g.name)));
    [...queueModel.cards.out, ...queueModel.cards.in].forEach(c => c.rows.forEach(x => names.set(x.id, c.merchant || c.title)));
    return names;
}
/** The rows a save labelled beyond the ones it was pressed for (the rule it
 *  made reached them too), said: " 1 more row took the same label: ADI MEDIA PTE LTD."
 *  waits: they came to a Check each row merchant, so they wait to be checked. */
function alsoLabelled(labelledIds, rows, { waits = false } = {}) {
    const own = new Set(rows.map(x => x.id));
    const extra = labelledIds.filter(id => !own.has(id));
    if (!extra.length) return '';
    const names = queueNames();
    const named = [...new Set(extra.map(id => names.get(id)).filter(Boolean))].slice(0, 2);
    const rest = extra.filter(id => !named.includes(names.get(id))).length;
    const who = named.length ? `: ${named.join(rest ? ', ' : ' and ')}${rest ? ` and ${rest} more` : ''}` : '';
    const did = !waits ? 'took the same label' : extra.length === 1 ? 'waits for you to check it' : 'wait for you to check them';
    return ` ${plural(extra.length, 'more row')} ${did}${who}.`;
}

// Book prefill (fin-surfaces 10): a book picker starts at the operator's last
// book for the merchant on that account, else a company's own account's
// company, else the type's book; a short hint says which. One read for every
// row the Queue shows, kept with the other reads until a write.
async function loadPrefills(ids) {
    const key = `book-prefill:${ids.join(',')}`;
    if (!ids.length) return new Map();
    if (!cache.has(key)) cache.set(key, fetch('/api/book-prefill', {
        method: 'POST', headers: { Accept: 'application/json', 'Content-Type': 'application/json' }, body: JSON.stringify({ tx_ids: ids }),
    }).then(res => res.ok ? res.json() : {}).then(d => new Map(Object.entries(d.prefill || {}).map(([id, p]) => [Number(id), p])))
        .catch(() => new Map()));
    return cache.get(key);
}
/** Where a row's prefilled book came from; none when it is the type's. */
function prefillHint(p) {
    if (p.reason === 'last') return `${p.book} last time on ${p.account}`;
    if (p.reason === 'account') return `${p.book} ${p.account_kind === 'card' ? 'card' : 'account'}`;
    return '';
}
/** One picker for several rows: {book, hint} when every row's prefill is the
 *  same book from history or its account; else null, and the type's book stands. */
function sharedPrefill(rows, prefills) {
    const got = rows.map(x => prefills.get(x.id));
    if (!got.length || got.some(p => !p || !p.book || p.reason === 'type' || p.book !== got[0].book)) return null;
    return { book: got[0].book, hint: [...new Set(got.map(prefillHint))].slice(0, 2).join('; ') };
}
function prefillHintHTML(prefill, book, cls) {
    return prefill?.hint ? `<span class="hint ${cls}"${book === prefill.book ? '' : ' hidden'}>${esc(prefill.hint)}</span>` : '';
}

function pickSaveHTML(card, books) {
    const pick = pickState.get(card.key);
    if (!pick) return '';
    return `${eachSegHTMLQ('pick-set', card.key, card.merchant, pick.each)}
        <select class="sel" data-change="pick-book" data-key="${esc(card.key)}" aria-label="${pick.each ? 'Usual book' : 'Book'} for ${esc(card.merchant)}"${queueBusy ? ' disabled' : ''}>${bookOptions(books, pick.book, 'Choose book')}</select>${prefillHintHTML(card.prefill, pick.book, 'q-pick-hint')}
        <button type="button" class="btn sm subtle" data-act="pick-save" data-key="${esc(card.key)}"${pick.book && !queueBusy && !recBlocks(card.key) ? '' : ' disabled'}>${icon('check')}Save</button>`;
}
/** Pick a type's line for the rule its save teaches (fin-surfaces 53). */
function pickRec(card) {
    const first = card.rows[0];
    return recShown(card.key, { tx: first.id, txs: card.rows.map(x => x.id), name: resolveBody(first, null).service_name, suggested: suggestPattern(first.description),
        changed: () => {
            const go = $('[data-act="pick-save"]', laneCardEl(card.key));
            if (go) go.disabled = queueBusy || !pickState.get(card.key)?.book || recBlocks(card.key);
        } });
}
function pickCardHTML(card, books) {
    queueGroups.set(card.key, card.rows);
    const pick = pickState.get(card.key);
    const types = card.guess.types.map(t => `<button type="button" class="pick" data-act="pick-type" data-key="${esc(card.key)}" data-type="${t.type_id}" aria-pressed="${pick ? pick.typeId === t.type_id : false}">${esc(t.name)} <em>${Math.round(t.probability * 100)}%</em></button>`).join('');
    return `<article class="qi qi--pick" data-key="${esc(card.key)}">
        <div class="qi__w"><p class="qi__t">${esc(card.title)}${rowMark(card.rows[0].id)}</p><p class="qi__m">${qiMeta(card, card.items[0].meta)}</p></div>
        <div class="qi__amt num">${esc(sumByCurrency(card.items))}</div>
        <div class="qi__act"><button type="button" class="btn sm" data-act="resolve" data-tx="${card.rows[0].id}" data-group="${esc(card.key)}">Another type…</button></div>
        <div class="qi__more">${pickRec(card)}<div class="picks" role="group" aria-label="Jev’s guesses for ${esc(card.merchant)}">${types}</div>
            <div class="pick-save"${pick ? '' : ' hidden'}>${pickSaveHTML(card, books)}</div></div>
        ${qiRowsHTML(card)}</article>`;
}
ACT['pick-type'] = el => {
    const card = laneCardByKey.get(el.dataset.key);
    if (!card || queueBusy) return;
    const typeId = Number(el.dataset.type);
    const was = pickState.get(card.key);
    pickState.set(card.key, { typeId, book: card.prefill?.book || queueModel.r.typeById.get(typeId)?.proposed_book || '',
        each: was ? was.each : merchantChecksEach(resolveBody(card.rows[0], null).service_name) });
    const box = laneCardEl(card.key);
    $$('.pick', box).forEach(b => b.setAttribute('aria-pressed', String(Number(b.dataset.type) === typeId)));
    const save = $('.pick-save', box);
    save.innerHTML = pickSaveHTML(card, queueModel.r.books);
    save.hidden = false;
};
ACT['pick-book'] = el => {
    const pick = pickState.get(el.dataset.key);
    if (!pick) return;
    pick.book = el.value;
    const hint = $('.q-pick-hint', laneCardEl(el.dataset.key));
    if (hint) hint.hidden = pick.book !== laneCardByKey.get(el.dataset.key)?.prefill?.book;
    const go = $('[data-act="pick-save"]', laneCardEl(el.dataset.key));
    if (go) go.disabled = queueBusy || !pick.book || recBlocks(el.dataset.key);
};
// Every row the same or Check each row: the card's rows are saved with the
// book shown either way (checked, under Check each row); new rows then wait.
ACT['pick-set'] = el => {
    const pick = pickState.get(el.dataset.key), card = laneCardByKey.get(el.dataset.key);
    if (!pick || !card || queueBusy) return;
    pick.each = el.dataset.v === 'each';
    const save = $('.pick-save', laneCardEl(card.key));
    save.innerHTML = pickSaveHTML(card, queueModel.r.books);
    $(`[data-act="pick-set"][data-v="${el.dataset.v}"]`, save)?.focus();
};
ACT['pick-save'] = async el => {
    const card = laneCardByKey.get(el.dataset.key), pick = pickState.get(el.dataset.key);
    if (!card || !pick || !pick.book || queueSaveWaits()) return;
    const type = card.guess.types.find(t => t.type_id === pick.typeId);
    queueSaving(laneCardEl(card.key));
    if (recBlocks(card.key)) return;
    const res = await saveResolve(resolveBody(card.rows[0], type.type_id, { visible: true, book: pick.book, each: pick.each,
        pattern: recWording(card.key, card.rows[0]) }), card.rows,
        { quiet: true, check: pick.each ? () => pick.book : null });
    queueBusy = false;
    if (res.ok) pickState.delete(card.key);
    savedToast(card.merchant, type.name, pick.book, res, card.rows, { each: pick.each });
    rerender(); refreshFrame();
};

// Check each row (fin-surfaces 12): the rows of a merchant set to check each
// row that are not checked yet, one card per merchant, one line per row with
// a type and a book picker. A line's ✓ checks that row; Confirm all checks
// every line as shown. Each is one bulk-label write (a field left as fin
// gave it is checked, one changed is chosen): one change, one Undo.
const eachPicks = new Map();   // a row's id -> {typeId, book} as changed on its line
/** fin's book for a line when it beats the row's own (its usual book): the
 *  last book on this account, or a company's own card (book prefill,
 *  fin-surfaces 10, read once for the Queue as queueModel.prefills). A
 *  prefill from the type is not shown here: the usual book stands. */
function eachPrefill(row) {
    const p = queueModel && queueModel.prefills ? queueModel.prefills.get(row.id) : null;
    if (!p || !p.book || (p.reason !== 'last' && p.reason !== 'account')) return null;
    // One home for the hint's words: prefillHint, as the sure card and Pick a type show it.
    return { book: p.book, hint: prefillHint(p) };
}
/** A line as it stands: its type and book, and the hint while the book is fin's prefill. */
function eachLine(row) {
    const pick = eachPicks.get(row.id) || {};
    const pre = eachPrefill(row);
    const book = pick.book ?? pre?.book ?? row.book ?? '';
    return { typeId: pick.typeId ?? row.type_id, book, hint: pre && pick.book === undefined ? pre.hint : '' };
}
const eachReady = line => !!(line.typeId && line.book);
const eachShut = new Set();    // the Check each row merchants folded shut (open by default)
function eachCardHTML(card, books) {
    const n = card.rows.length;
    const limit = queueOpen[card.key] || LINES_PAGE;
    const types = queueModel.r.types;
    const off = queueBusy ? ' disabled' : '';
    const newest = [...card.rows].sort((a, b) => b.date.localeCompare(a.date) || b.id - a.id);
    // One line per row on the side's grid: its date and account, amount and
    // ✓; under them a quiet type picker, the book picker and the hint.
    const lines = newest.slice(0, limit).map(x => {
        const line = eachLine(x), when = esc(day(x.date));
        return `<div class="ce" data-tx="${x.id}">
            <div class="ce__when">${esc(day(x.date, { year: false }))} · ${esc(x.account_name || '')}${rowMark(x.id)}</div>
            <div class="ce__amt num">${esc(money(Math.abs(toMinor(x.amount_sgd, x.currency)), x.currency))}</div>
            <div class="ce__go"><button type="button" class="btn sm subtle icon" data-act="each-ok" data-tx="${x.id}" data-key="${esc(card.key)}" aria-label="Confirm the ${when} row"${eachReady(line) && !queueBusy ? '' : ' disabled'}>${icon('check')}</button></div>
            <div class="ce__pick"><select class="sel sel--quiet" data-change="each-type" data-tx="${x.id}" data-key="${esc(card.key)}" aria-label="Type for the ${when} row"${off}>${typeOptions(types, line.typeId)}</select>
                <select class="sel" data-change="each-book" data-tx="${x.id}" data-key="${esc(card.key)}" aria-label="Book for the ${when} row"${off}>${bookOptions(books, line.book, 'Choose book')}</select>
                <span class="hint">${esc(line.hint)}</span></div></div>`;
    }).join('');
    const ready = card.rows.every(x => eachReady(eachLine(x)));
    const svcId = card.rows[0].service_id;
    const usual = (queueModel.services || []).find(s => s.id === svcId)?.book || card.rows[0].book || '';
    const open = !eachShut.has(card.key), id = `ce-${card.key}`;
    return `<article class="qi qi--each" data-key="${esc(card.key)}">
        <div class="qi__w"><p class="qi__t">${rowDetailToggle({ id, label: card.merchant, open, act: 'each-open', data: { key: card.key } })}</p>
            <p class="qi__m">${plural(n, 'row')} · ${esc(spanWords(card.rows))}${usual ? ` · usual book ${esc(usual)}` : ''}${svcId ? ` · <a class="link" href="#/books/merchant/${svcId}">its page</a>` : ''}</p></div>
        <div class="qi__amt num">${esc(sumByCurrency(card.items))}</div>
        <div class="qi__act"><button type="button" class="btn sm subtle" data-act="each-save" data-key="${esc(card.key)}"${ready && !queueBusy ? '' : ' disabled'}>${icon('check')}Confirm all</button></div>
        <div class="qi__more" id="${esc(id)}"${open ? '' : ' hidden'}>${lines}
            ${n > limit ? `<div class="q-show"><button type="button" class="btn sm ghost" data-act="queue-more" data-lane="${esc(card.key)}">Show ${plural(n - limit, 'more line')}</button></div>` : ''}</div></article>`;
}
ACT['each-open'] = el => { if (toggleRowDetail(el)) eachShut.delete(el.dataset.key); else eachShut.add(el.dataset.key); };
/** A pick redraws its line's hint and the card's buttons in place (focus stays). */
function eachPick(el, k, v) {
    const id = Number(el.dataset.tx);
    eachPicks.set(id, { ...eachPicks.get(id), [k]: v });
    const card = laneCardByKey.get(el.dataset.key), box = laneCardEl(el.dataset.key);
    if (!card || !box) return;
    const x = card.rows.find(y => y.id === id), line = eachLine(x);
    const ce = $(`.ce[data-tx="${id}"]`, box);
    $('.hint', ce).textContent = line.hint;
    $('[data-act="each-ok"]', ce).disabled = queueBusy || !eachReady(line);
    $('[data-act="each-save"]', box).disabled = queueBusy || !card.rows.every(y => eachReady(eachLine(y)));
}
ACT['each-type'] = el => eachPick(el, 'typeId', Number(el.value) || null);
ACT['each-book'] = el => eachPick(el, 'book', el.value);
/** Check rows as their lines show them: one bulk-label write. A refusal
 *  writes nothing, so every line stays waiting, as picked. */
async function saveEach(card, rows) {
    queueSaving(laneCardEl(card.key));
    const r = await send('POST', '/api/transactions/bulk-label', {
        rows: rows.map(x => { const line = eachLine(x); return { tx_id: x.id, type_id: line.typeId, book: line.book }; }),
    });
    queueBusy = false;
    if (r.ok) rows.forEach(x => eachPicks.delete(x.id));
    else toast(r.data.error || 'That did not work; nothing was changed', { bad: true, ms: 9000 });
    return r;
}
ACT['each-ok'] = async el => {
    const card = laneCardByKey.get(el.dataset.key);
    const x = card && card.rows.find(y => y.id === Number(el.dataset.tx));
    if (!x || queueSaveWaits()) return;
    const line = eachLine(x);
    if (!eachReady(line)) return;
    const r = await saveEach(card, [x]);
    const type = queueModel.r.typeById.get(line.typeId);
    if (r.ok) toast(`Saved the ${day(x.date)} row of ${card.merchant}: ${type?.display_name || type?.name || 'its type'} · ${line.book}.`, { undo: r.change });
    rerender(); refreshFrame();
};
ACT['each-save'] = async el => {
    const card = laneCardByKey.get(el.dataset.key);
    if (!card || !card.rows.every(x => eachReady(eachLine(x))) || queueSaveWaits()) return;
    const r = await saveEach(card, card.rows);
    if (r.ok) toast(`Saved ${plural(card.rows.length, 'row')} of ${card.merchant}.`, { undo: r.change });
    rerender(); refreshFrame();
};

/** The Check each row step's one note: "not checked" is said here, once. */
const EACH_NOTE = 'Each row came in with its merchant’s type and usual book, not checked. Confirm it, or change its type or book.';
/** One side: its head, then a section that folds for each step with rows,
 *  all on one grid (what | amount | action) so the columns line up. */
async function laneHTML(lane, list, { books, ask, empty }) {
    const folded = new Set(store.get('queueFolded', []));
    const rows = list.reduce((n, c) => n + c.rows.length, 0);
    const sections = await Promise.all(QUEUE_STEPS.map(async s => {
        const cards = list.filter(c => c.step === s.k);
        if (!cards.length) return '';
        const key = `${lane}-${s.k}`, open = !folded.has(key);
        const limit = queueOpen[key] || QUEUE_PAGE;
        const drawn = await Promise.all(cards.slice(0, limit).map(c =>
            c.step === 'pick' ? pickCardHTML(c, books) : c.step === 'mixed' ? eachCardHTML(c, books) : queueCardHTML(c)));
        const more = cards.length - limit;
        const suggest = s.k === 'noguess' && ask ? '<button type="button" class="btn sm ghost" data-act="suggest-types">Suggest types…</button>' : '';
        return `<section class="step">
            <h3 class="step-h"><button type="button" class="fold" data-act="q-fold" data-sec="${key}" aria-expanded="${open}" aria-controls="q-sec-${key}">${esc(s.label)} <span class="n">${cards.length}</span>${icon('caret-right', 'caret')}</button>${suggest}</h3>
            <div class="step-b" id="q-sec-${key}"${open ? '' : ' hidden'}>${s.k === 'mixed' ? `<p class="step-note">${EACH_NOTE}</p>` : ''}${drawn.join('')}
                ${more > 0 ? `<div class="q-show"><button type="button" class="btn sm ghost" data-act="queue-more" data-lane="${key}">Show ${more} more</button></div>` : ''}</div></section>`;
    }));
    const xb = lane === 'in' ? (await window.expectQueueStep?.(list)) || '' : '';
    return `<section class="card q-lane" id="lane-${lane}" aria-labelledby="lh-${lane}">
        <header class="lane-h">${dirSquare(lane)}<h2 id="lh-${lane}">${lane === 'out' ? 'Money out' : 'Money in'}</h2><span class="sub">${list.length} to do · ${plural(rows, 'row')}</span></header>
        <div class="lane-body">${xb}${list.length ? sections.join('') : `<p class="lane-empty">${esc(empty)}</p>`}</div></section>`;
}
ACT['q-fold'] = el => {
    const open = el.getAttribute('aria-expanded') !== 'true';
    const folded = new Set(store.get('queueFolded', []));
    if (open) folded.delete(el.dataset.sec); else folded.add(el.dataset.sec);
    store.set('queueFolded', [...folded]);
    el.setAttribute('aria-expanded', String(open));
    const body = document.getElementById(el.getAttribute('aria-controls'));
    if (body) body.hidden = !open;
};

// The rule shown when teaching (fin-surfaces 53; spec 24, cases 27-31):
// wherever a merchant is taught (the sure card, Pick a type, "This was…")
// the line says the wording its rule will recognise and how many rows that
// wording labels, read from the preview function's count (GET
// /api/rules/reach, debounced while the wording is typed). A wording that
// matches only the row taught (a booking number kept in it, 19), nothing, or
// rows another merchant holds says so at once in the warning colour; one
// that matches nothing cannot be saved. Saving a wording that takes another
// merchant's rows goes through the preview (resolve answers 409).
const REC_WAIT = 250;          // ms after the last key before the count is read
const recWords = new Map();    // a teach's key -> the wording as changed on its line
const recOpen = new Set();     // the keys whose wording is open to change
const recFound = new Map();    // a teach's key -> {wording, reach}: reach null while it is read
const recCtx = new Map();      // a teach's key -> {tx, txs, name, suggested, changed}
const recTimers = new Map();
/** The wording a teach saves: as changed on its line, else fin's suggestion. */
function recWording(key, row) { return recWords.has(key) ? recWords.get(key) : suggestPattern(row.description); }
/** What a reach warns of, each in the warning colour: only the row taught,
 *  rows another merchant holds. '' when nothing. */
function recWarn(reach) {
    if (!reach || reach.error || !reach.count) return '';
    const warns = [];
    if (reach.only_this) warns.push('· only this one: a number in the wording?');
    if ((reach.others || []).length) warns.push(`· also ${reach.others.map(o => `${plural(o.count, 'row')} now ${o.name}’s`).join(', ')}`);
    return warns.map(w => `<span class="warn-word">${esc(w)}</span>`).join(' ');
}
/** The live count under a wording being changed: the whole book's rows the
 *  wording matches, "matches 3 rows in the book". */
function recCountHTML(reach) {
    if (!reach) return '<span class="muted">counting…</span>';
    if (reach.error) return `<span class="warn-word">${esc(reach.error)}</span>`;
    if (!reach.count) return '<span class="warn-word">matches no row: check the wording</span>';
    const warn = recWarn(reach);
    return `matches ${plural(reach.count, 'row')} in the book${warn ? ` ${warn}` : ''}`;
}
/** The meta line under a folded sure line's merchant: "recognises INDIGOAIR".
 *  Opened, the detail's line says it, so this one is not shown. */
function recMiniHTML(wording, key = '') {
    return wording ? `<span class="rec-mini"${key ? ` data-rec-mini="${esc(key)}"` : ''}>recognises <b class="mono">${esc(wording)}</b></span>` : '';
}
/** The line in a teach's detail: "Recognises rows containing INDIGOAIR ·
 *  3 rows in the book, 2 of them here · change", or, open to change, the
 *  wording in place with its live count. The count is the whole book's;
 *  here is the rows the teach holds (the sure card's line), named only when
 *  they are fewer. A teach with no here says only "N rows in the book". */
function recLineHTML(key, wording, reach, editing, here) {
    const id = `rec-${String(key).replace(/[^A-Za-z0-9]/g, '_')}`;
    if (editing) {
        return `<div class="rec rec--edit" data-rec-line="${esc(key)}"><label class="cap" for="${id}">Recognises rows containing</label>
            <div class="rec__row"><input type="text" id="${id}" class="mono" data-rec="${esc(key)}" value="${esc(wording)}" autocomplete="off" spellcheck="false">
            <button type="button" class="btn sm subtle" data-act="rec-done" data-key="${esc(key)}">Done</button></div>
            <p class="rec__n" data-rec-n="${esc(key)}" aria-live="polite">${recCountHTML(reach)}</p></div>`;
    }
    const warn = recWarn(reach);
    const some = here && here < reach?.count ? `, ${here} of them here` : '';
    const count = !reach ? '' : reach.error || !reach.count ? ` · ${recCountHTML(reach)}` : ` · ${plural(reach.count, 'row')} in the book${some}${warn ? ` ${warn}` : ''}`;
    return `<div class="rec" data-rec-line="${esc(key)}"><span class="cap">Recognises rows containing</span> <b class="mono">${esc(wording)}</b>${count} · <button type="button" class="link" data-act="rec-edit" data-key="${esc(key)}">change</button></div>`;
}
function recKeyWording(key) {
    const ctx = recCtx.get(key);
    return recWords.has(key) ? recWords.get(key) : ctx ? ctx.suggested : '';
}
/** The reach read for the wording a teach shows now; null while it is read. */
function recReach(key) {
    const f = recFound.get(key);
    return f && f.wording === recKeyWording(key) ? f.reach : null;
}
/** A teach's wording cannot be saved: it matches no row, or is refused. */
function recBlocks(key) {
    const reach = recReach(key);
    return !!(reach && (reach.error || !reach.count));
}
/** Read the count for a wording, after REC_WAIT ms of quiet (0: at once). */
function recLoad(key, wording, wait = REC_WAIT) {
    let ctx = recCtx.get(key);
    if (ctx?.of) ctx = recCtx.get(ctx.of);   // an added wording: its line's
    clearTimeout(recTimers.get(key));
    recFound.set(key, { wording, reach: null });
    if (!ctx) return;
    recTimers.set(key, setTimeout(async () => {
        const q = new URLSearchParams({ pattern: wording, match_type: 'contains', service_name: ctx.name || '' });
        (ctx.txs || [ctx.tx]).slice(0, 200).forEach(id => q.append('tx_id', String(id)));
        let reach;
        try { reach = await get(`/api/rules/reach?${q}`); } catch (e) { reach = { error: e.message }; }
        const now = recFound.get(key);
        if (!now || now.wording !== wording) return;   // a later wording is being read
        now.reach = reach;
        recDraw(key);
    }, wait));
}
/** The line for a teach, as drawn: its wording, its count (read once if not
 *  yet), open or not. ctx: {tx, txs, name, suggested, changed, here}: txs the
 *  rows the save is for (only this one: the wording matches one of them and
 *  no other row), tx when it is one; changed() draws
 *  the save's state again once the count is known. '' without a wording. */
function recShown(key, ctx) {
    recCtx.set(key, ctx);
    const wording = recKeyWording(key);
    if (!wording && !recOpen.has(key)) return '';
    const f = recFound.get(key);
    if (wording && (!f || f.wording !== wording)) recLoad(key, wording, 0);
    return recLineHTML(key, wording, recReach(key), recOpen.has(key), ctx.here);
}
/** A teach's line drawn again in place: the count, or the whole line. */
function recDraw(key, { whole = false } = {}) {
    const ctx = recCtx.get(key);
    if (!ctx) return;
    if (ctx.of) { recMoreDraw(ctx.of); recCtx.get(ctx.of)?.changed?.(); return; }
    const box = $$('[data-rec-line]').find(x => x.dataset.recLine === key);
    if (box && recOpen.has(key) && !whole) {
        const n = $('.rec__n', box);
        if (n) n.innerHTML = recCountHTML(recReach(key));
    } else if (box) {
        const at = document.createElement('div');
        at.innerHTML = recLineHTML(key, recKeyWording(key), recReach(key), recOpen.has(key), ctx.here);
        box.replaceWith(at.firstElementChild);
    } else recWhenShown(key);
    $$('[data-rec-mini]').filter(x => x.dataset.recMini === key).forEach(x => { x.outerHTML = recMiniHTML(recKeyWording(key), key); });
    if (ctx.changed) ctx.changed();
}
// A count can come back before its line is in the page: the Queue is built
// as a string while its other reads finish, then put in. Such a line is
// drawn again as soon as it is in the page (walk lp-r1, case 31).
const recLate = new Set();     // the keys whose count came back before their line was in the page
let recWatch = null;           // the page's observer, kept only while a line is late
function recWhenShown(key) {
    recLate.add(key);
    if (recWatch) return;
    recWatch = new MutationObserver(() => {
        [...recLate].forEach(k => {
            if (!recReach(k)) { recLate.delete(k); return; }   // a later wording is read; its own count draws it
            if (!$$('[data-rec-line]').some(x => x.dataset.recLine === k)) return;
            recLate.delete(k);
            recDraw(k);
        });
        if (!recLate.size) { recWatch.disconnect(); recWatch = null; }
    });
    recWatch.observe(document.body, { childList: true, subtree: true });
}

// Several wordings (queue-naming C, cases 14-18): the sure card's opened line
// adds wordings under its own with "+ another wording", each with its own
// field, live count and warning (the same reach read) and a remove link; the
// save sends them with its own, one rule each, in one change. An empty
// wording, or one the line already has, is refused before saving.
const recMore = new Map();     // a teach's key -> the keys of the wordings added under it
let recMoreN = 0;
/** Why an added wording cannot be saved, before its count: it is empty, or
 *  the line already has it (ignoring case). '' when it can. */
function recMoreRefused(key, xk) {
    const wording = recKeyWording(xk).toUpperCase();
    if (!wording) return 'Say the wording the rule recognises';
    const keys = recMore.get(key) || [];
    const before = [recKeyWording(key), ...keys.slice(0, keys.indexOf(xk)).map(recKeyWording)];
    return before.some(w => (w || '').toUpperCase() === wording) ? 'Already a wording here' : '';
}
/** An added wording's words: why it is refused, else its live count. */
function recMoreCountHTML(key, xk) {
    const refused = recMoreRefused(key, xk);
    if (refused) return `<span class="warn-word">${esc(refused)}</span>`;
    const f = recFound.get(xk), wording = recKeyWording(xk);
    if (!f || f.wording !== wording) recLoad(xk, wording, 0);
    return recCountHTML(recReach(xk));
}
/** The wordings added under a teach's line, then the link that adds one. */
function recMoreHTML(key) {
    const lines = (recMore.get(key) || []).map(xk => {
        const id = `rec-${xk.replace(/[^A-Za-z0-9]/g, '_')}`;
        return `<div class="rec rec--edit rec--more" data-rec-line="${esc(xk)}"><label class="sr-only" for="${id}">Another wording</label>
            <div class="rec__row"><input type="text" id="${id}" class="mono" data-rec-more="${esc(xk)}" value="${esc(recKeyWording(xk))}" autocomplete="off" spellcheck="false">
            <button type="button" class="link" data-act="rec-more-remove" data-key="${esc(xk)}" aria-label="Remove the wording">remove</button></div>
            <p class="rec__n" data-rec-n="${esc(xk)}" aria-live="polite">${recMoreCountHTML(key, xk)}</p></div>`;
    }).join('');
    return `<div class="rec-more" data-rec-more-of="${esc(key)}">${lines}<button type="button" class="link" data-act="rec-more" data-key="${esc(key)}">+ another wording</button></div>`;
}
/** The wordings a teach's save sends beside its own. */
function recMoreWordings(key) { return (recMore.get(key) || []).map(recKeyWording); }
/** An added wording cannot be saved: refused, or by its reach read refused or
 *  matching no row, as the first wording (recBlocks); not while it is read. */
function recMoreBlocks(key) { return (recMore.get(key) || []).some(xk => recMoreRefused(key, xk) || recBlocks(xk)); }
/** Each added wording's words drawn again in place (a wording before it may have changed). */
function recMoreDraw(key) {
    (recMore.get(key) || []).forEach(xk => {
        const n = $$('[data-rec-n]').find(x => x.dataset.recN === xk);
        if (n) n.innerHTML = recMoreCountHTML(key, xk);
    });
}
/** The list drawn again whole, then the line's save state. */
function recMoreRedraw(key) {
    const box = $$('[data-rec-more-of]').find(x => x.dataset.recMoreOf === key);
    if (box) box.outerHTML = recMoreHTML(key);
    recCtx.get(key)?.changed?.();
}
/** A saved line's added wordings, gone. */
function recMoreForget(key) {
    (recMore.get(key) || []).forEach(xk => { clearTimeout(recTimers.get(xk)); [recWords, recFound, recCtx, recTimers].forEach(m => m.delete(xk)); });
    recMore.delete(key);
}
ACT['rec-more'] = el => {
    const key = el.dataset.key, xk = `${key}+${++recMoreN}`;
    recMore.set(key, [...(recMore.get(key) || []), xk]);
    recWords.set(xk, '');
    recCtx.set(xk, { of: key });
    recMoreRedraw(key);
    $$('[data-rec-more]').find(x => x.dataset.recMore === xk)?.focus();
};
ACT['rec-more-word'] = el => {
    const xk = el.dataset.recMore, ctx = recCtx.get(xk);
    if (!ctx) return;
    const wording = el.value.trim();
    recWords.set(xk, wording);
    if (wording && !recMoreRefused(ctx.of, xk)) recLoad(xk, wording);
    recDraw(xk);
};
ACT['rec-more-remove'] = el => {
    const xk = el.dataset.key, key = recCtx.get(xk)?.of;
    if (!key) return;
    clearTimeout(recTimers.get(xk));
    [recWords, recFound, recCtx, recTimers].forEach(m => m.delete(xk));
    recMore.set(key, recMore.get(key).filter(k => k !== xk));
    recMoreRedraw(key);
    $$('[data-act="rec-more"]').find(x => x.dataset.key === key)?.focus();
};
document.addEventListener('input', e => {
    const el = e.target.closest && e.target.closest('[data-rec-more]');
    if (el) ACT['rec-more-word'](el);
});
ACT['rec-edit'] = el => {
    recOpen.add(el.dataset.key);
    recDraw(el.dataset.key, { whole: true });
    const input = $$('[data-rec]').find(x => x.dataset.rec === el.dataset.key);
    if (input) { input.focus(); input.select(); }
};
ACT['rec-done'] = el => {
    recOpen.delete(el.dataset.key);
    recDraw(el.dataset.key, { whole: true });
    $$('[data-act="rec-edit"]').find(x => x.dataset.key === el.dataset.key)?.focus();
};
document.addEventListener('input', e => {
    const el = e.target.closest('[data-rec]');
    if (!el) return;
    const key = el.dataset.rec, wording = el.value.trim();
    recWords.set(key, wording);
    if (wording) recLoad(key, wording);
    else recFound.set(key, { wording, reach: { error: 'Say the wording the rule recognises' } });
    recDraw(key);
});
document.addEventListener('keydown', e => {
    const el = e.target.closest && e.target.closest('[data-rec]');
    if (el && e.key === 'Enter') { e.preventDefault(); ACT['rec-done']({ dataset: { key: el.dataset.rec } }); }
});

// The "Jev is sure" card (fin-surfaces 05): one row per merchant group whose
// stored guess is sure, across both sides, each saved with today's guess save
// and the book its picker shows; "Save all" is the same save in a loop. A line
// reads the merchant its save makes and opens on its name to the rest: how
// new rows arrive, Something else… ("This was…" for its rows, fin-surfaces
// 09) and its rows. A line set to Check each row gives its rows their own
// book pickers, saved checked (fin-surfaces 13). Drawn as folio's banded
// table (fin-surfaces 17): merchant · rows · amount · Jev's type · book · ✓.
const SURE_NOTE = 'Saving teaches fin each merchant: its rows here, and every new row from it, get the type and book shown, without asking again.';
const SURE_ROWS = 5;           // a line's rows shown before "Show N more rows"
const sureBooks = new Map();   // a group's key -> the book picked for it ('' = none yet)
const sureShown = new Set();   // the keys of the lines opened to their detail
const sureAll = new Set();     // the keys of the lines showing every row
const sureSets = new Map();    // a group's key -> true for Check each row, false for Every row the same, as picked
const sureRowBooks = new Map(); // a row's id -> the book picked on its line under Check each row
const sureNames = new Map();   // a group's key -> the name as typed in its Name field
let sureList = [];             // the groups as last drawn, in card order

// Teaching with Check each row (fin-surfaces 13): wherever a merchant is
// taught (the sure card, Pick a type, "This was…") the setting is offered;
// it starts as the merchant's own when fin already holds it.
function merchantChecksEach(name, services = queueModel?.services || []) {
    const svc = services.find(s => s.name.toLowerCase() === String(name || '').toLowerCase());
    return !!(svc && svc.review_each_time);
}
/** A line's setting: as picked, else the merchant's own. */
function sureEach(g) { return sureSets.has(g.key) ? sureSets.get(g.key) : !!g.checksEach; }
/** A row's book on a line set to Check each row: as picked on it, else fin's
 *  prefill from history or the card's company, else the line's usual book. */
function sureRowBook(g, x) {
    if (sureRowBooks.has(x.id)) return sureRowBooks.get(x.id);
    return eachPrefill(x)?.book || sureBook(g);
}
function sureRowHint(x) { return sureRowBooks.has(x.id) ? '' : eachPrefill(x)?.hint || ''; }
/** The rows a line's detail draws, oldest first: the first few until
 *  "Show N more rows". Under Check each row only these are checked on save;
 *  the rest wait in the Queue's Check each row step. */
function sureRows(g) {
    const rows = g.items.map(i => i.row);
    return sureAll.has(g.key) ? rows : rows.slice(0, SURE_ROWS);
}
/** Ready to save: a name, a book, every wording savable, and under Check each row a book on every row drawn. */
function sureReady(g) { return !!g.name && !!sureBook(g) && !recBlocks(g.key) && !recMoreBlocks(g.key) && (!sureEach(g) || sureRows(g).every(x => sureRowBook(g, x))); }

// The name on the card (queue-naming B): the opened line's Name field holds
// the name its save makes, fin's suggestion until it is changed, with a
// chooser of the merchants fin holds. A name that is one of them, ignoring
// case, attaches the save to it: its own type and book are what the save
// uses, and the line shows them (a merchant with none keeps the line's).
/** The merchant fin holds by the line's name, ignoring case; null when none. */
function sureHeld(g) {
    const name = (g.name || '').toLowerCase();
    return name ? (queueModel?.services || []).find(s => s.name.toLowerCase() === name) || null : null;
}
/** The name a line shows: the held merchant's own spelling, else as typed. */
function sureShownName(g) { return sureHeld(g)?.name || g.name; }
/** The type a line saves: the held merchant's, else Jev's. held: it is the merchant's. */
function sureType(g) {
    const held = sureHeld(g);
    const type = held?.type_id && queueModel.r.typeById.get(held.type_id);
    return type ? { type_id: type.id, name: type.display_name || type.name, held: true } : g.type;
}
/** The book is the held merchant's own: its picker shows it and cannot change it. */
function sureBookHeld(g) { return !!sureHeld(g)?.book; }
/** The words under the Name field: the merchant it saves to, or that it needs a name. */
function sureNameSay(g) {
    if (!g.name) return '<span class="warn-word">Give it a name</span>';
    const held = sureHeld(g);
    if (!held) return 'Type to find a merchant already in fin.';
    return `Saves to ${esc(held.name)}, already in fin: ${esc(sureType(g).name)} · ${sureBook(g) ? esc(sureBook(g)) : 'the book you pick'}`;
}

/** The merchant a line's save makes: the name suggested from its first
 *  row's text, or the merchant fin already holds by that name, ignoring case
 *  (the save reuses it). asks: a wording that names no merchant gives no
 *  name, for a sheet whose save asks for one (This was…); else resolve's. */
function sureName(row, services, { asks = false } = {}) {
    const name = asks ? suggestName(row.description) : resolveBody(row, null).service_name;
    if (!name) return '';
    return services.find(s => s.name.toLowerCase() === name.toLowerCase())?.name || name;
}
/** The lanes' rows with no type whose guess is sure, one group per merchant
 *  string and side: money out first, then money in, each by amount. */
function sureGroups(q, guesses, r, services) {
    const groups = [];
    ['out', 'in'].forEach(lane => {
        const byMerchant = new Map();
        q.lanes[lane].forEach(i => {
            if (i.kind !== 'untyped') return;
            const g = guesses.get(i.row.id);
            if (!g || g.route !== 'prefill' || !g.types || !g.types.length) return;
            let group = byMerchant.get(g.merchant);
            if (!group) {
                const top = g.types[0];
                group = { key: `${lane}|${g.merchant}`, lane, merchant: g.merchant, type: top, items: [], amount: 0,
                    proposed: r.typeById.get(top.type_id)?.proposed_book || null };
                byMerchant.set(g.merchant, group);
            }
            group.items.push(i);
            group.amount += i.amount;
        });
        // A line's rows run oldest first, and the first names the merchant its save makes.
        byMerchant.forEach(group => {
            group.items.sort((a, b) => (a.row.date || '').localeCompare(b.row.date || '') || a.row.id - b.row.id);
            group.suggested = sureName(group.items[0].row, services, { asks: true });
            group.name = sureNames.has(group.key) ? sureNames.get(group.key).trim() : group.suggested;
            group.checksEach = merchantChecksEach(group.name, services);
        });
        groups.push(...[...byMerchant.values()].sort((a, b) => b.amount - a.amount));
    });
    return groups;
}
/** A line's prefilled book, with its hint. Under Check each row (queue-naming
 *  D) it is the book the latest of its rows carries (by date, then id), as
 *  that row's picker starts: each row is checked with its own, so the usual
 *  book does not wait on a pick. Else fin's book prefill for every row. */
function surePrefill(g) {
    if (!sureEach(g)) return g.prefill;
    // The rows run oldest first: the last that carries a book is the latest
    // (it has a source; an empty book reads as Household and is not one).
    // Its own book, as case 19 says, not its picker's prefill; with no row
    // carrying one, the latest picker's prefill with its own hint.
    const rows = g.items.map(i => i.row);
    const held = rows.filter(x => x.sources?.book).pop();
    if (held) return { book: held.book, hint: `${held.book} on the latest row` };
    return rows.map(eachPrefill).filter(Boolean).pop() || g.prefill;
}
/** The book a group would be saved with: the held merchant's, else the one
 *  picked, else its prefill, else its type's. */
function sureBook(g) {
    const held = sureHeld(g);
    if (held?.book) return held.book;
    return sureBooks.has(g.key) ? sureBooks.get(g.key) : (surePrefill(g)?.book || g.proposed || '');
}
/** The title row's words and its button, from what is ready now. */
function sureHead() {
    const ready = sureList.filter(sureReady).length;
    const rows = sureList.reduce((n, g) => n + g.items.length, 0);
    const need = sureList.length - ready;
    return {
        ready,
        meta: `${plural(rows, 'row')} across both sides.${need ? ` ${need} ${need === 1 ? 'needs' : 'need'} a book first.` : ''}`,
        label: ready === sureList.length ? 'Save all' : `Save ${ready} ready`,
    };
}
/** Jev's name for a line, only when it differs from the merchant's beyond
 *  letter case ("GRAB" for Grab is not news; ruling D3). */
function jevName(g) { return g.merchant.toLowerCase() === g.name.toLowerCase() ? '' : g.merchant; }
/** The one sentence for a line's setting, then Jev's name when it differs. */
function sureSay(g) {
    const book = sureBook(g) ? esc(sureBook(g)) : 'the book you pick';
    const name = g.name ? esc(sureShownName(g)) : 'this merchant', type = esc(sureType(g).name);
    const say = sureEach(g)
        ? `You give the rows below their own book. Each new row from ${name} arrives as ${type} in its usual book, ${book}, marked not checked, and waits in the Queue’s Check each row step.`
        : `Every new row from ${name} will be ${type} · ${book}, without asking.`;
    const jev = jevName(g);
    return `${say}${jev ? ` Jev’s name for it: ${esc(jev)}.` : ''}`;
}
/** A line's detail (folio rowDetail): how new rows arrive, with Something
 *  else… at the right; the sentence for that setting; its rows (oldest
 *  first), with a book picker per row under Check each row. */
function sureDetailHTML(g, at) {
    const each = sureEach(g), rows = g.items.map(i => i.row), shown = sureRows(g);
    const pick = x => {
        const when = esc(day(x.date)), hint = sureRowHint(x);
        return `<td class="sl-rbk"><select class="sel" data-change="sure-row-book" data-key="${esc(g.key)}" data-tx="${x.id}" aria-label="Book for the ${when} row"${queueBusy ? ' disabled' : ''}>${bookOptions(queueModel.r.books, sureRowBook(g, x), 'Choose book')}</select>${hint ? `<span class="hint">${esc(hint)}</span>` : ''}</td>`;
    };
    const first = g.items[0].row;
    const rec = recShown(g.key, { tx: first.id, txs: g.items.map(i => i.row.id), name: g.name, suggested: suggestPattern(first.description),
        here: g.items.length, changed: drawSureState });
    const nameId = `sn-${at}`;
    const field = `<div class="sname"><label class="cap" for="${nameId}">Name</label>
        <input type="text" id="${nameId}" data-sure-name="${esc(g.key)}" list="sure-merchants" value="${esc(g.name)}" autocomplete="off" spellcheck="false" aria-describedby="${nameId}-say"${queueBusy ? ' disabled' : ''}>
        <p class="sname__say" id="${nameId}-say" data-sure-said="${esc(g.key)}" aria-live="polite">${sureNameSay(g)}</p></div>`;
    return `<div class="det">${field}${rec}${rec ? recMoreHTML(g.key) : ''}
        <div class="det__top"><div class="det__set"><span class="cap" id="nr-${at}">New rows</span>${eachSegHTMLQ('sure-set', g.key, g.name, each, { labelledBy: `nr-${at}` })}</div>
            <button type="button" class="btn sm ghost" data-act="sure-else" data-key="${esc(g.key)}">Something else…</button></div>
        <p class="det__say">${sureSay(g)}</p>
        ${rowsTableHTML(shown, `${g.name}: its rows`, { book: each ? pick : null })}
        ${rows.length > shown.length ? `<button type="button" class="btn sm ghost det__more" data-act="sure-all-rows" data-key="${esc(g.key)}">Show ${plural(rows.length - shown.length, 'more row')}</button>` : ''}</div>`;
}
/** A line: the name opens its detail; the setting shows only as a tag, and
 *  only when it is Check each row; the select alone says it needs a book. */
function sureRowHTML(g, books, at) {
    const book = sureBook(g), each = sureEach(g), type = sureType(g), held = sureBookHeld(g);
    const n = g.items.length, open = sureShown.has(g.key), id = `sd-${at}`;
    return `<tr class="sl${open ? ' is-open' : ''}" data-key="${esc(g.key)}" data-at="${at}">
        <td class="sl-m">${rowDetailToggle({ id, label: sureShownName(g) || 'Give it a name', open, act: 'sure-open', data: { key: g.key } })}${open ? '' : recMiniHTML(recWording(g.key, g.items[0].row), g.key)}${each ? '<span class="tag each">Check each row</span>' : ''}</td>
        <td class="r num sl-n">${n}<span class="ph-w"> ${n === 1 ? 'row' : 'rows'}</span></td>
        <td class="r num sl-a">${esc(sumByCurrency(g.items))}</td>
        <td class="sl-ty">${esc(type.name)} ${type.held ? '<em>in fin</em>' : `<em>${Math.round(type.probability * 100)}%</em>`}</td>
        <td class="sl-bk"><select class="sel${book ? '' : ' need'}" data-change="sure-book" data-key="${esc(g.key)}" aria-label="${each ? 'Usual book' : 'Book'} for ${esc(g.name)}"${queueBusy || held ? ' disabled' : ''}>${bookOptions(books, book, 'Choose book')}</select>${prefillHintHTML(surePrefill(g), book, 'sl-hint')}</td>
        <td class="sl-go"><button type="button" class="btn sm subtle icon" data-act="sure-save" data-key="${esc(g.key)}" aria-label="Save ${esc(g.name)}"${sureReady(g) && !queueBusy ? '' : ' disabled'}>${icon('check')}</button></td></tr>
        <tr class="sl-det" id="${id}"${open ? '' : ' hidden'}><td colspan="6">${open ? sureDetailHTML(g, at) : ''}</td></tr>`;
}
function sureCardHTML(books) {
    if (!sureList.length) return '';
    const open = store.get('sureOpen', true);
    const h = sureHead();
    // Many merchants show a page at a time; Save all still saves every ready one.
    const limit = queueOpen.sure || LINES_PAGE;
    const more = sureList.length - limit;
    // Money out and money in are section rows, each with its count.
    const rows = [];
    let side = '';
    sureList.slice(0, limit).forEach((g, at) => {
        if (g.lane !== side) {
            side = g.lane;
            rows.push(`<tr class="sec"><td colspan="6">${dirSquare(side)}${side === 'out' ? 'Money out' : 'Money in'} · ${sureList.filter(x => x.lane === side).length}</td></tr>`);
        }
        rows.push(sureRowHTML(g, books, at));
    });
    const table = bandedTable({ caption: 'Merchants Jev is sure of', cls: 'sure-t', colgroup: [0, 64, 124, 200, 150, 64], rows,
        columns: [{ label: 'Merchant' }, { label: 'Rows', cls: 'r' }, { label: 'Amount', cls: 'r' }, { label: 'Jev’s type' }, { label: 'Book' }, { label: 'Save', sr: true }] });
    // One chooser for every line's Name field: the merchants fin holds.
    const chooser = `<datalist id="sure-merchants">${(queueModel?.services || []).map(s => `<option value="${esc(s.name)}">`).join('')}</datalist>`;
    return `<section class="card sure-card" id="sure-card" aria-labelledby="sure-title">${chooser}
        <div class="sure-head">
            <div><h2 id="sure-title"><button type="button" class="disc sure-fold" data-act="sure-fold" aria-expanded="${open}" aria-controls="sure-body">${icon('caret-right', 'caret')}<span>Jev is sure of ${esc(plural(sureList.length, 'merchant'))}</span></button></h2>
                <p class="sub"><span id="sure-meta">${esc(h.meta)}</span> ${SURE_NOTE}</p></div>
            <button type="button" class="btn primary" data-act="sure-all" id="sure-all"${h.ready && !queueBusy ? '' : ' disabled'}>${esc(h.label)}</button>
        </div>
        <div class="sure-body" id="sure-body"${open ? '' : ' hidden'}>${table}
            ${more > 0 ? `<div class="q-show"><button type="button" class="btn sm ghost" data-act="queue-more" data-lane="sure">Show ${more} more</button></div>` : ''}</div></section>`;
}
/** Readiness and the busy state, drawn in place (a pick keeps its focus). */
function drawSureState() {
    const card = $('#sure-card');
    if (!card) return;
    sureList.forEach(g => {
        const tr = $$('tr.sl', card).find(x => x.dataset.key === g.key);
        if (!tr) return;
        const ready = sureReady(g);
        $('[data-change="sure-book"]', tr).classList.toggle('need', !sureBook(g));
        $$('select', tr).forEach(x => { x.disabled = queueBusy || sureBookHeld(g); });
        $$('select, .seg button, [data-sure-name]', tr.nextElementSibling).forEach(x => { x.disabled = queueBusy; });
        const hint = $('.sl-hint', tr);
        if (hint) hint.hidden = sureBook(g) !== surePrefill(g)?.book;
        $('[data-act="sure-save"]', tr).disabled = queueBusy || !ready;
    });
    const h = sureHead();
    $('#sure-meta').textContent = h.meta;
    const all = $('#sure-all');
    all.textContent = h.label;
    all.disabled = queueBusy || !h.ready;
}
ACT['sure-fold'] = el => {
    const open = el.getAttribute('aria-expanded') !== 'true';
    store.set('sureOpen', open);
    el.setAttribute('aria-expanded', String(open));
    $('#sure-body').hidden = !open;
};
/** A line drawn again in place (its setting changed): focus goes back to the control changed. */
function drawSureLine(g, focus) {
    const tr = $$('#sure-card tr.sl').find(x => x.dataset.key === g.key);
    if (!tr) return;
    const body = document.createElement('tbody');
    body.innerHTML = sureRowHTML(g, queueModel.r.books, Number(tr.dataset.at));
    tr.nextElementSibling.replaceWith(body.rows[1]);
    tr.replaceWith(body.rows[0]);
    drawSureState();
    if (focus) $$(`#sure-card ${focus}`).find(x => x.dataset.key === g.key)?.focus();
}
/** Its detail drawn again (the usual book changed, or every row asked for):
 *  the sentence names the book; picked rows keep theirs. */
function drawSureRows(g) {
    const tr = $$('#sure-card tr.sl').find(x => x.dataset.key === g.key);
    const box = tr && tr.nextElementSibling;
    if (box && sureShown.has(g.key)) box.cells[0].innerHTML = sureDetailHTML(g, Number(tr.dataset.at));
}
ACT['sure-book'] = el => {
    sureBooks.set(el.dataset.key, el.value);
    const g = sureList.find(x => x.key === el.dataset.key);
    if (g) drawSureRows(g);
    drawSureState();
};
// Every row the same | Check each row, in the line's detail: the line is
// drawn again (its tag, its rows' book pickers) and stays open.
ACT['sure-set'] = el => {
    const g = sureList.find(x => x.key === el.dataset.key);
    if (!g || queueBusy) return;
    sureSets.set(g.key, el.dataset.v === 'each');
    sureShown.add(g.key);
    drawSureLine(g, `[data-act="sure-set"][data-v="${el.dataset.v}"]`);
};
// The Name field, typed in or chosen from: the line's name, the merchant it
// attaches to, and what that sets (its type, its book, how its rows arrive),
// drawn again in place; the field keeps its focus.
ACT['sure-name'] = el => {
    const g = sureList.find(x => x.key === el.dataset.sureName);
    if (!g) return;
    sureNames.set(g.key, el.value);
    g.name = el.value.trim();
    g.checksEach = merchantChecksEach(g.name);
    const tr = $$('#sure-card tr.sl').find(x => x.dataset.key === g.key);
    if (tr) {
        const body = document.createElement('tbody');
        body.innerHTML = sureRowHTML(g, queueModel.r.books, Number(tr.dataset.at));
        const box = tr.nextElementSibling;
        tr.replaceWith(body.rows[0]);
        const said = $('[data-sure-said]', box);
        if (said) said.innerHTML = sureNameSay(g);
        const say = $('.det__say', box);
        if (say) say.innerHTML = sureSay(g);
    }
    drawSureState();
};
document.addEventListener('input', e => {
    const el = e.target.closest && e.target.closest('[data-sure-name]');
    if (el) ACT['sure-name'](el);
});
ACT['sure-row-book'] = el => {
    const g = sureList.find(x => x.key === el.dataset.key);
    if (!g) return;
    const id = Number(el.dataset.tx);
    sureRowBooks.set(id, el.value);
    el.parentElement.querySelector('.hint')?.remove();
    drawSureState();
};
// A line opens on its name to its detail and folds back in place; neither writes anything.
ACT['sure-open'] = el => {
    const g = sureList.find(x => x.key === el.dataset.key);
    const tr = el.closest('tr');
    const box = document.getElementById(el.getAttribute('aria-controls'));
    if (!g || !tr || !box) return;
    const open = el.getAttribute('aria-expanded') !== 'true';
    if (open) sureShown.add(g.key); else sureShown.delete(g.key);
    // Drawn afresh: the usual book may have changed while it was folded.
    if (open) box.cells[0].innerHTML = sureDetailHTML(g, Number(tr.dataset.at));
    // "recognises" is said once: under the name folded, in the detail opened.
    const mini = $('[data-rec-mini]', tr);
    if (open) mini?.remove();
    else if (!mini) el.insertAdjacentHTML('afterend', recMiniHTML(recWording(g.key, g.items[0].row), g.key));
    toggleRowDetail(el);
    tr.classList.toggle('is-open', open);
    if (open) drawSureState();
};
ACT['sure-all-rows'] = el => {
    const g = sureList.find(x => x.key === el.dataset.key);
    if (!g) return;
    sureAll.add(g.key);
    drawSureRows(g);
};
// Jev is wrong: "This was…" for the line's rows, Jev's guess already turned down.
ACT['sure-else'] = el => {
    const g = sureList.find(x => x.key === el.dataset.key);
    if (!g || queueSaveWaits()) return;
    const rows = g.items.map(i => i.row);
    openWhatWas(rows[0], rows, { notGuess: g.type.name });
};
/** One group saved with today's guess save and the book shown. Rows an
 *  earlier save in the same press already labelled are left out; a group
 *  left with none is skipped. Under Check each row only the rows drawn are
 *  checked with their books; the others wait. */
async function saveSureGroup(g, labelled) {
    const rows = g.items.map(i => i.row).filter(x => !labelled.has(x.id));
    if (!rows.length) return { skipped: true };
    const each = sureEach(g);
    const drawn = new Set(sureRows(g).map(x => x.id));
    const res = await saveResolve(resolveBody(rows[0], sureType(g).type_id, { merchant: g.name, visible: true, book: sureBook(g), each,
        pattern: recWording(g.key, g.items[0].row), wordings: recMoreWordings(g.key) }), rows,
        { quiet: true, check: each ? x => sureRowBook(g, x) : null, onScreen: each ? rows.filter(x => drawn.has(x.id)) : null });
    res.labelledIds.forEach(id => labelled.add(id));
    return res;
}
ACT['sure-save'] = async el => {
    const g = sureList.find(x => x.key === el.dataset.key);
    if (!g || !sureReady(g) || queueSaveWaits()) return;
    const book = sureBook(g), each = sureEach(g);
    queueSaving();
    const res = await saveSureGroup(g, new Set());
    queueBusy = false;
    if (!res.skipped) {   // skipped: its rows already have a type
        if (res.ok) sureForget(g);
        // Under Check each row the rows not drawn are named as waiting.
        savedToast(sureShownName(g), sureType(g).name, book, res, each ? sureRows(g) : g.items.map(i => i.row), { each });
    }
    rerender(); refreshFrame();
};
/** A saved line's picks are let go. */
function sureForget(g) {
    sureBooks.delete(g.key);
    sureSets.delete(g.key);
    sureNames.delete(g.key);
    recMoreForget(g.key);
    g.items.forEach(i => sureRowBooks.delete(i.row.id));
}
/** One merchant's save said: the sure card's ✓, Pick a type's Save and "This
 *  was…". Rows the rule reached beyond the merchant's own (rows) are named;
 *  under Check each row they wait to be checked. */
function savedToast(merchant, typeName, book, res, rows, { each = false } = {}) {
    if (res.cancelled) { toast(res.error); return; }
    const said = each ? `Saved ${merchant} as ${typeName}, usually ${book}` : `Saved ${merchant} as ${typeName} · ${book}`;
    const then = each ? '; each new row will wait for you to check it.' : ', now and from now on.';
    if (res.ok) toast(`${said}${then}${alsoLabelled(res.labelledIds, rows, { waits: each })}${pastWords(merchant, res.past)}`, { undo: res.changes });
    else if (res.resolved) toast(`${said}; then: ${res.error}`, { bad: true, undo: res.changes, ms: 9000 });
    else toast(res.error, { bad: true, undo: res.changes, ms: 9000 });
}
/** What the answer to the offer did to a held merchant's past rows, for a toast. */
function pastWords(merchant, past) {
    if (!past) return '';
    const n = past.n, rows = `${merchant}’s ${plural(n, 'past row')}`;
    return past.answer === 'leave' ? ` ${rows} ${n === 1 ? 'is' : 'are'} marked checked, labels unchanged.`
        : ` ${rows} ${n === 1 ? 'waits' : 'wait'} in the Queue too.`;
}
ACT['sure-all'] = async () => {
    const ready = sureList.filter(sureReady);
    if (!ready.length || queueSaveWaits()) return;
    queueSaving();
    const labelled = new Set(), changes = [];
    const by = new Map();      // a row's id -> the merchant whose save labelled it
    const skipped = [];        // [merchant, the merchant whose save took its rows]
    const each = [];           // the merchants saved as Check each row
    let pasts = '';            // what the offer's answers did to past rows
    let saved = 0, cancelled = 0, error = null, at = ready.length;
    for (const [n, g] of ready.entries()) {
        const checks = sureEach(g), name = sureShownName(g);   // as fin holds it, not as typed
        const res = await saveSureGroup(g, labelled);
        if (res.skipped) { skipped.push([name, by.get(g.items[0].row.id)]); continue; }
        if (res.cancelled) { cancelled += 1; continue; }   // its question closed: that line is left as it was
        pasts += pastWords(name, res.past);
        changes.push(...res.changes);
        res.labelledIds.forEach(id => { if (!by.has(id)) by.set(id, name); });
        // A merchant whose rule was made counts as saved even if one of its
        // rows then failed: its changes are in the Undo.
        if (res.resolved) { sureForget(g); saved += 1; if (checks) each.push(name); }
        if (!res.ok) { error = res.error; at = n + 1; break; }
    }
    queueBusy = false;
    if (error) {
        // Out of: the merchants tried, and those after that still had rows to save.
        const left = ready.slice(at).filter(g => g.items.some(i => !labelled.has(i.row.id))).length;
        toast(`Saved ${saved} of ${plural(at - skipped.length + left, 'merchant')}; then: ${error}`, { bad: true, undo: changes, ms: 9000 });
    } else if (saved) {
        const took = skipped.slice(0, 2).map(([m, from]) => ` ${m} took ${from}’s label.`).join('');
        const rest = skipped.length > 2 ? ` ${plural(skipped.length - 2, 'more merchant')} took an earlier one’s label.` : '';
        const outside = alsoLabelled([...labelled], ready.flatMap(g => g.items.map(i => i.row)));
        const names = each.length > 1 ? `${each.slice(0, -1).join(', ')} and ${each[each.length - 1]}` : each[0];
        const how = !each.length ? '; their new rows will be labelled the same way.'
            : `. New rows from ${names} will wait for you to check them${saved > each.length ? '; the rest will be labelled the same way' : ''}.`;
        toast(`Saved ${plural(saved, 'merchant')}${how}${took}${rest}${outside}${pasts}`, { undo: changes });
    } else if (cancelled) toast('Nothing was saved.');
    else toast('Nothing left to save: those rows already have a type.');
    rerender(); refreshFrame();
};

let queueSearch = '';          // the merchant search: kept while the page is open, never stored
let queueModel = null;         // what the Queue read, so a chip, a search or "Show more" redraws in place
const laneCardByKey = new Map();
let workToken = 0;

/** The part of the Queue a search or a step chip redraws: the sure card,
 *  the chips and both sides, each showing what the search and chip leave. */
async function workHTML() {
    const { r, ask, sure, cards } = queueModel;
    const needle = queueSearch.trim().toLowerCase();
    const hit = text => !needle || String(text || '').toLowerCase().includes(needle);
    sureList = sure.filter(g => hit(g.name) || hit(g.merchant));
    queueGroups.clear();
    laneCardByKey.clear();
    const all = [...cards.out, ...cards.in];
    all.forEach(c => laneCardByKey.set(c.key, c));
    let step = store.get('queueStep', '');
    if (!all.some(c => c.step === step)) step = '';
    const found = { out: cards.out.filter(c => hit(c.find)), in: cards.in.filter(c => hit(c.find)) };
    const counts = {};
    [...found.out, ...found.in].forEach(c => { counts[c.step] = (counts[c.step] || 0) + 1; });
    const chip = (k, label, n) => `<button type="button" class="chip" data-act="q-step" data-step="${k}" aria-pressed="${step === k}">${esc(label)}${k ? ` <span class="n">${n}</span>` : ''}</button>`;
    const chips = all.length ? `<div class="chips q-steps" role="group" aria-label="Steps">${chip('', 'All steps')}${QUEUE_STEPS.filter(s => counts[s.k] || s.k === step).map(s => chip(s.k, s.label, counts[s.k] || 0)).join('')}</div>` : '';
    const shown = { out: step ? found.out.filter(c => c.step === step) : found.out, in: step ? found.in.filter(c => c.step === step) : found.in };
    const lanes = await Promise.all(['out', 'in'].map(lane => {
        const empty = !cards[lane].length ? 'Nothing that needs thought.'
            : needle ? `Nothing here matches “${queueSearch.trim()}”.` : `Nothing here under ${step && QUEUE_STEPS.find(s => s.k === step).label}.`;
        return laneHTML(lane, shown[lane], { books: r.books, ask, empty });
    }));
    // On a phone one side shows at a time; the desk hides the toggle and shows both.
    const side = store.get('queueSide', 'out') === 'in' ? 'in' : 'out';
    const sides = `<div class="seg q-side" role="group" aria-label="Show">${['out', 'in'].map(lane =>
        `<button type="button" data-act="q-side" data-side="${lane}" aria-pressed="${side === lane}">${lane === 'out' ? 'Money out' : 'Money in'} <span class="n">${shown[lane].length}</span></button>`).join('')}</div>`;
    return `${sureCardHTML(r.books)}${chips}${sides}<div class="waits-cols q-cols" data-side="${side}">${lanes.join('')}</div>`;
}
ACT['q-side'] = el => {
    store.set('queueSide', el.dataset.side);
    $$('[data-act="q-side"]').forEach(b => b.setAttribute('aria-pressed', String(b === el)));
    const cols = $('.q-cols');
    if (cols) cols.dataset.side = el.dataset.side;
};
async function drawWork({ focus = '' } = {}) {
    const token = ++workToken;
    const html = await workHTML();
    const box = $('#q-work');
    if (token !== workToken || !box) return;
    box.innerHTML = html;
    if (focus) $(focus, box)?.focus();
}
/** What else waits, on Books: each count opens it with "Needs a look" on. */
function alsoHTML(q) {
    const refused = q.lanes.books.filter(i => i.kind === 'refused').length;
    const figures = q.lanes.books.length - refused;
    const parts = [[refused, plural(refused, 'refused statement')], [q.lanes.aside.length, `${q.lanes.aside.length} set aside`],
        [figures, `${plural(figures, 'figure')} to look at`]].filter(([n]) => n);
    if (!parts.length) return '';
    return `<p class="q-also">Also waiting, in Books: ${parts.map(([, words]) =>
        `<button type="button" class="link" data-act="needs-look-link">${esc(words)}</button>`).join('<span aria-hidden="true">·</span>')}</p>`;
}

async function viewQueue() {
    // The book prefills need only the Queue's rows, so they are asked the moment
    // those are in, alongside the guesses, merchants and switch still on the way.
    // Every row a book picker could show is asked (rows with no type, rows to
    // check); one that ends up with no picker is read and left unused.
    const queue = loadQueue();
    const prefillsRead = queue.then(q => loadPrefills(['out', 'in'].flatMap(lane =>
        q.lanes[lane].filter(i => i.kind === 'untyped' || i.kind === 'mixed').map(i => i.row.id))));
    const [q, guesses, r, ask, services, prefills] = await Promise.all([queue, loadGuesses(), refs(), suggestionsOn(), servicesList(), prefillsRead]);
    // The sure groups sit in their card, so they leave the lanes below.
    const sure = sureGroups(q, guesses, r, services);
    const sureIds = new Set(sure.flatMap(g => g.items.map(i => i.row.id)));
    const left = lane => q.lanes[lane].filter(i => !(i.kind === 'untyped' && sureIds.has(i.row.id)));
    const cards = { out: laneCards('out', left('out'), guesses), in: laneCards('in', left('in'), guesses) };
    // The prefills read above, on every row a book picker shows: the sure card,
    // Pick a type and the rows each checked on their own (queueModel.prefills).
    const lines = [...cards.out, ...cards.in].filter(c => c.step === 'pick' || c.step === 'mixed');
    sure.forEach(g => { g.prefill = sharedPrefill(g.items.map(i => i.row), prefills); });
    lines.forEach(c => { if (c.step === 'pick') c.prefill = sharedPrefill(c.rows, prefills); });
    queueModel = { r, ask, sure, cards, prefills, services };
    const todo = sure.length + cards.out.length + cards.in.length;
    const rows = sure.reduce((n, g) => n + g.items.length, 0) + [...cards.out, ...cards.in].reduce((n, c) => n + c.rows.length, 0);
    // A redraw while the search has focus keeps it, and where the caret was.
    const typing = document.activeElement && document.activeElement.id === 'q-search' ? document.activeElement : null;
    if (typing) {
        const [from, to] = [typing.selectionStart, typing.selectionEnd];
        afterRender(() => { const box = $('#q-search'); if (box) { box.focus(); box.setSelectionRange(from, to); } });
    }
    // The head and the merchant search on one line (the search under it on a phone).
    const head = `<div class="page-head q-head"><div><h1>Queue</h1><p>${todo ? `${todo} to do · ${plural(rows, 'row')}` : 'Nothing to do'}</p></div>
        <label class="search-field q-search"><span class="sr-only">Find a merchant</span>${icon('magnifying-glass')}
            <input type="search" id="q-search" value="${esc(queueSearch)}" placeholder="Find a merchant" autocomplete="off"></label></div>
        ${q.capped.length ? `<p class="notice warn small">${icon('warning')}<span>Too many to read at once, so the counts and sums here are short: ${esc(q.capped.join('; '))}.</span></p>` : ''}`;
    return `<div class="queue">${head}<div class="q-work" id="q-work">${await workHTML()}</div>${alsoHTML(q)}</div>`;
}
ACT['queue-more'] = el => { queueOpen[el.dataset.lane] = 9999; drawWork(); };
ACT['q-step'] = el => { store.set('queueStep', el.dataset.step); drawWork({ focus: `[data-act="q-step"][data-step="${el.dataset.step}"]` }); };
document.addEventListener('input', e => {
    if (e.target.id !== 'q-search') return;
    queueSearch = e.target.value;
    drawWork();
});

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

/** The merchant text a save suggests as its rule (and, title-cased, its name).
 *  It stops at a reference: any later word carrying a digit ("312-4567890",
 *  "#4471"), a reference glued on after "*" ("GRAB*A-R12345678"), or a long
 *  code. Before a reference the first word stands alone when it is plainly
 *  a name: 6+ letters ("INDIGOAIR"), or 4+ with a mark ("GRAB*", "BUS/MRT");
 *  a short plain word ("SP 1234", "KOPI A-R123") keeps the next word too, so
 *  its rule does not reach other merchants' rows ("SQ *KOPI CORNER"). */
function suggestPattern(description) {
    let pattern = (description || '').toUpperCase();
    if (/\b(PAYNOW|FAST PAYMENT|TRANSFER|I-BANK|GIRO)\b/.test(pattern)) return '';
    pattern = pattern.replace(/\s+(SG|SGP|SIN|US|USA|GB|GBR|AU|AUS)\s*$/i, '').replace(/\s+\d{2}\/\d{2}$/, '');
    const stands = word => {
        const letters = (word.match(/[A-Z]/g) || []).length;
        return letters >= 6 || (letters >= 4 && /[^A-Z]/.test(word));
    };
    const parts = pattern.split(/\s+/);
    const glued = parts[0].match(/^(\S*?\w\*)\S*\d/);
    if (glued && stands(glued[1])) return glued[1];
    let cutoff = parts.length, ref = false;
    for (let i = 1; i < parts.length; i++) {
        ref = /\d/.test(parts[i]);
        if (ref || /^[A-Z0-9]{8,}$/.test(parts[i])) { cutoff = i; break; }
    }
    const least = ref && stands(parts[0]) ? 1 : 2;
    return parts.slice(0, Math.max(cutoff, least)).join(' ');
}
function titleCase(s) { return s.toLowerCase().replace(/\b\w/g, c => c.toUpperCase()); }

/** The name a teach suggests for a merchant: fin's own tidy-up of the
 *  statement wording (queue-naming A; suggest.py's clean, which is what Jev
 *  is sent, stays as it is). Its rules are the data below. Empty when the
 *  wording names no merchant (a PayNow, a transfer): the save asks for one. */
const NAME_RULES = {
    // a payee or transfer shape (suggest.py's _PAYEE_SHAPE): no merchant to name
    payee: /PAY ?NOW|PAYLAH|\bFAST\b|\bIBG\b|GIRO|TRANSFER|\bTRF\b|INWARD|REMITT|\bATM\b|\bCASH\b|\bFUNDS\b|I-BANK|:IB\b|\bMEP\b|\bFT\d/,
    // bank advice, card payment and fee or tax lines (suggest.py's _BANK_LINE): rules, not merchants
    bank: /^(ADVICE|BILL PAYMENT|PAYMENT\b|PAYMT\b|CARD PAYMENT|SERVICE CHARGE|MISC DEBIT|BUSINESS ADVANCE|INTEREST|CASH REBATE|SALARY|DIVIDEND|GST\b|FINANCE CHARGE|CCY CONVERSION FEE|FEE$)|\b(SERVICE FEE|ADMINISTRATIVE FEE|FOREIGN CURRENCY TRANSACTION FEE|ANNUAL (MEMBERSHIP )?FEE|LATE (PAYMENT )?(CHARGE|FEE)|ADMIN(ISTRATION)? FEE|OVERLIMIT FEE|GOODS (AND|&) SERVICES TAX|((CREDIT )?CARDS?|CC) (PAYMENT|PAYMT))\b/,
    // processors that print their name before the merchant's (suggest.py's, with SP and EZYPAY)
    // (SP Group, the power company, is a merchant: its SP is not the prefix)
    processor: /^(?:(?:2C2P|2C2|PAYPAL|PP|STRIPE|SQUARE|SQ|OMISE|GLOBAL-E|GLOBALE|ADYEN|SUMUP|ATOME|SMP|PYU|EZYPAYSGD|EZYPAY)\s*[*/]\s*|(?:2C2P|PAYPAL|STRIPE|OMISE|GLOBAL-E|GLOBALE|SP)\s+(?!(?:GROUP|SERVICES)\b)(?=\S))/,
    // a web address, its subdomains dropped ("HELP.UBER.COM" is Uber's)
    web: /^(?:WWW\.)?(?:[A-Z0-9-]+\.)*?([A-Z0-9-]+)\.(?:COM|NET|ORG|IO|CO|AI|APP|SHOP|STORE)(?:\.[A-Z]{2})?\/?$/,
    branch: /\s+-\s+.*$/,
    // trailing countries, cities and currencies (suggest.py's _PLACES, and the currencies)
    trailing: /(\s+(SINGAPORE|SGP|SGD|SG|SIN|USA|USD|US|GBR|GBP|GB|UK|LONDON|AUS|AUD|AU|SYDNEY|IND|INR|IN|HKG|HKD|HK|JPN|JPY|JP|TOKYO|MYS|MYR|MY|THA|THB|TH|BANGKOK|IDN|IDR|ID|NLD|NL|IRL|IE|DUBLIN|CAN|CAD|CA|DEU|DE|FRA|FR|EUR|EU|CHF|NZD|CNY))+$/,
};
function suggestName(description) {
    let text = (description || '').toUpperCase().replace(/\s+/g, ' ').trim().replace(/ ?\* ?/g, '*');
    if (NAME_RULES.payee.test(text)) return '';
    text = text.replace(NAME_RULES.processor, '');
    let words = text.split(' ');
    if (!/[A-Z]/.test(words[0])) return '';
    // "X*REST": a web address X names the merchant ("ETSY.COM*MULTIPLE SHOP");
    // any other X keeps the rest, so "GOOGLE*ADS" is not cut to "Google"
    const star = words[0].indexOf('*');
    if (star > 0 && NAME_RULES.web.test(words[0].slice(0, star))) words = [words[0].slice(0, star)];
    else words = words.join(' ').replace(/\*/g, ' ').trim().split(' ');
    words = words.map((w, i) => {
        const web = NAME_RULES.web.exec(w);
        return !web ? w : i === 0 ? web[1] : '';
    }).filter(Boolean);
    // every word from the first after the name's own with a digit is a reference
    const ref = words.findIndex((w, i) => i > 0 && /\d/.test(w));
    if (ref > 0) words = words.slice(0, ref);
    text = words.join(' ').replace(NAME_RULES.branch, '').replace(NAME_RULES.trailing, '').replace(/^[\s*\-\/.]+|[\s*\-\/.]+$/g, '');
    if (!/[A-Z]/.test(text) || NAME_RULES.bank.test(text)) return '';
    return text.toLowerCase().replace(/(^|[\s\-\/&(])([a-z])/g, (m, at, c) => at + c.toUpperCase());
}

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
/** notGuess: the type Jev guessed and the operator turned down (the sure
 *  card's Something else…), so the guess is not offered again. */
/** This was…'s line for the rule its save teaches (fin-surfaces 53): a
 *  fresh step starts from fin's suggestion. */
function wwRec() {
    const { row } = step;
    return recShown('ww', { tx: row.id, txs: step.group.map(x => x.id), name: ($('#rs-merchant')?.value || '').trim() || step.merchant, suggested: step.pattern,
        changed: () => { if (step && step.recWords) step.recWords(); } });
}
async function openWhatWas(row, group, { mixed = false, notGuess = '' } = {}) {
    recWords.delete('ww'); recOpen.delete('ww'); recFound.delete('ww');
    const [r, incomeKinds, services, q] = await Promise.all([refs(), get('/api/types?kind=income'), servicesList(), loadQueue()]);
    const minor = toMinor(row.amount_sgd, row.currency);
    const untyped = row.flow_type !== 'review';
    const pattern = suggestPattern(row.description);
    const merchant = row.service_name || sureName(row, services, { asks: true });
    const waiting = q.lanes.out.concat(q.lanes.in).filter(i => i.kind === 'untyped' && i.row.id !== row.id);
    const others = pattern ? waiting.filter(i => (i.row.description || '').toUpperCase().includes(pattern)).length : group.length - 1;
    let guess;
    if (notGuess) guess = { html: '', visible: true };
    else if (untyped) guess = await showResolveSuggestion(row, { n: group.length, where: 'step' });
    else guess = transferGuessHTML(row, await transferGuess(row), { where: 'step' });
    step = { row, group, untyped, mixed, choice: null, more: false, merchant, pattern, others, r, incomeKinds, services,
        suggestionVisible: !!(guess && guess.visible), guessed: !!(notGuess || (guess && guess.html)) };
    const title = `${minor < 0 ? 'Money in' : 'Money out'} · what was it?`;
    openSheet(title, `${rowHead(row, { more: group.length - 1 })}
        <div id="ww-guess">${guess.html || ''}</div>
        <p class="ww-label" id="ww-label">${notGuess ? `Not ${esc(notGuess)}? Something else` : guess.html ? 'Or something else' : 'What was it?'}</p>
        <div class="choices" id="ww-choices"></div>
        <div id="ww-ask"></div>`, { eyebrow: group.length > 1 ? `This was… · all ${group.length} rows` : 'This was…' });
    drawChoices();
}
function drawChoices() {
    const { r, choice, row } = step;
    // "A loan to a person" retired (personal-ledger 23, the contract). In its
    // place one row is offered Expect some back… (money out) or Expect to pay
    // back (money in), which opens the expect drawer on the row.
    const one = step.group.length === 1;
    const expect = row.amount_minor < 0
        ? { label: 'expect to pay back', description: 'money to give back to someone: it is not income' }
        : { label: 'expect some back…', description: 'part or all of it is coming back from someone: that part is not spending' };
    const all = one ? r.review.choices.flatMap(c => c.name === 'gift' ? [c, expect] : [c]) : r.review.choices;
    const tile = c => c === expect
        ? `<button type="button" class="choice" data-act="expect-mark" data-tx="${row.id}">
        <strong>${esc(sentence(c.label))}</strong><span>${esc(c.description)}</span></button>`
        : `<button type="button" class="choice" data-act="ww-pick" data-choice="${esc(c.name)}">
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
/** The type This was… offers first: the row's own; on a waiting row, which
 *  carries its merchant and no type (fin-redesign 05), its merchant's. An
 *  untyped spending or refund row offers none, the picker blank. */
function offeredType(row, services) {
    if (row.flow_type !== 'review') return row.type_id ?? null;
    return row.type_id ?? (services || []).find(s => s.id === row.service_id)?.type_id ?? null;
}
ACT['ww-more'] = () => { step.more = true; drawChoices(); $('#ww-choices .choice:nth-child(4)')?.focus(); };
ACT['ww-change'] = () => { step.choice = null; drawChoices(); $('#ww-choices .choice')?.focus(); };
ACT['ww-pick'] = el => {
    const { r, row, untyped, incomeKinds } = step;
    const choice = r.review.choices.find(c => c.name === el.dataset.choice);
    step.choice = choice;
    drawChoices();
    let fields = '';
    // Spending, every row from this merchant, teaches it: Every row the same,
    // or Check each row (fin-surfaces 13), shown with that scope only.
    const teaches = untyped && choice.name === 'spending';
    if (choice.asks === 'type') {
        fields = `<label class="field"><span>Type</span><select id="cw-type">${typeOptions(r.types, offeredType(row, step.services))}</select></label>
            <label class="field"><span id="cw-book-l">Book</span><select id="cw-book">${bookOptions(r.books, null, untyped ? 'As the merchant or type says' : 'As the type says')}</select></label>
            ${teaches ? `<label class="field" id="cw-set-f"><span>New rows from it</span><select id="cw-set">
                <option value="same">Every row the same</option><option value="each"${merchantChecksEach(step.merchant, step.services) ? ' selected' : ''}>Check each row</option></select></label>` : ''}`;
    } else if (choice.asks === 'account') {
        const allowed = r.accounts.filter(a => choice.kinds.includes(a.type) && !step.group.some(x => x.account_id === a.id) && a.status !== 'archived');
        fields = `<label class="field"><span>Which account</span><select id="cw-account"><option value="">Choose</option>${allowed.map(a => `<option value="${a.id}">${esc(a.name)} (${esc(a.currency)})</option>`).join('')}</select></label>`;
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
                    <label class="check"><input type="radio" name="rs-scope" value="rule"> Only rows with the wording it recognises</label></fieldset></div>
            <div id="ww-rec">${wwRec()}</div>`;
    } else if (untyped) {
        scope = `<p class="small muted">${n > 1 ? `Labels these ${n} rows.` : 'Labels this row only.'}</p>`;
    }
    $('#ww-ask').innerHTML = `<div class="fields">${fields}</div>${scope}
        <button class="btn primary block" id="ww-save" data-act="ww-save">Save: ${esc(choice.label)}</button>`;
    const words = () => {
        const w = $('#ww-scope-words');
        if (!w) return;
        const s = $('input[name="rs-scope"]:checked')?.value;
        const set = $('#cw-set-f');
        if (set) {
            set.hidden = s !== 'service_default';
            $('#cw-book-l').textContent = !set.hidden && $('#cw-set').value === 'each' ? 'Usual book' : 'Book';
        }
        const name = $('#rs-merchant')?.value.trim() || step.merchant;
        w.textContent = s === 'transaction' ? 'Label only this row'
            : s === 'rule' ? `Label rows whose text contains ${recKeyWording('ww') || '…'}` : `Label every row from ${name || 'this merchant'} the same way`;
        // The rule's wording is shown wherever this save teaches one; its
        // count names other merchants' rows by the merchant taught.
        const ctx = recCtx.get('ww');
        if (ctx && ctx.name !== name) { ctx.name = name; if (recKeyWording('ww')) recLoad('ww', recKeyWording('ww')); }
        const rec = $('#ww-rec');
        if (rec) rec.hidden = s === 'transaction';
        const save = $('#ww-save');
        if (save) save.disabled = s !== 'transaction' && recBlocks('ww');
    };
    step.recWords = words;
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
/** What resolve takes for a row with no type and the type chosen for it.
 *  each: the merchant setting as taught (true: Check each row); left out,
 *  the merchant keeps its own. wordings: the others its rule recognises. */
function resolveBody(row, typeId, { merchant, scope = 'service_default', pattern, book = '', visible = false, each, wordings = [] } = {}) {
    const name = merchant || row.service_name || suggestName(row.description) || titleCase(suggestPattern(row.description) || row.description || '');
    const body = { tx_id: row.id, service_name: name, type_id: typeId, apply_scope: scope,
        pattern: pattern ?? suggestPattern(row.description), match_type: 'contains', suggestion_visible: visible };
    if (book) body.book = book;
    if (typeof each === 'boolean' && scope === 'service_default') body.check_each_row = each;
    if (wordings.length && scope !== 'transaction') body.wordings = wordings;
    return body;
}
/** Save a type for a row with no type. A merchant text too plain to make a
 *  rule from (a PayNow or a GIRO) labels the card's other rows one by one.
 *  Returns {ok, resolved, changes, labelled, labelledIds, book, error}: whether the
 *  type was saved for the row (resolved; ok also needs the card's other rows),
 *  the change ids it wrote, newest last, the rows it labelled and the
 *  merchant's book, and past ({answer, n}) when a merchant fin held was asked
 *  about its past rows (cancelled: the question was closed, nothing was
 *  written). quiet: no toast and no redraw, for a caller that words its
 *  own (the sure card). check: taught as Check each row, the rows on screen
 *  (onScreen, else the group, else the row) are then checked with their books
 *  (check(row), or the merchant's): one bulk-label write; rows the rule
 *  reached beyond them wait. */
async function saveResolve(body, group = [], { quiet = false, check = null, onScreen = null } = {}) {
    const services = await servicesList();
    const svc = services.find(s => s.name.toLowerCase() === body.service_name.toLowerCase());
    if (svc) body.service_id = svc.id;
    // Taught as Check each row, a merchant fin already holds as Every row the
    // same asks first what becomes of its past rows (12's offer, once); the
    // answer goes in the same change. Closing the question saves nothing.
    let past = null;
    if (body.check_each_row === true && svc && !svc.review_each_time && svc.rows_to_check) {
        const answer = await askPast(svc);
        if (!answer) return { ok: false, resolved: false, cancelled: true, changes: [], labelled: 0, labelledIds: [], error: 'Nothing was saved.' };
        body.past = answer;
        past = { answer, n: svc.rows_to_check };
    }
    // Teaching a merchant fin holds a new default reaches its past rows
    // through the preview, as the merchant sheet does (fin-surfaces 21).
    const r = await sendPreviewed('POST', '/api/transactions/resolve', body, { key: 'past_rows', taught: body.tx_id });
    if (r.cancelled) return { ok: false, resolved: false, cancelled: true, changes: [], labelled: 0, labelledIds: [], error: 'Nothing was saved.' };
    if (!r.ok) {
        const error = r.data.error || 'That did not work; nothing was changed';
        if (!quiet) toast(error, { bad: true });
        return { ok: false, resolved: false, changes: [], labelled: 0, labelledIds: [], error };
    }
    if (!quiet) { toast(resolvedWords(r, 'Saved'), { undo: r.change }); refreshFrame(); }
    const changes = r.change ? [r.change] : [];
    // The group's other rows the rule did not reach (a merchant text too plain
    // for a rule, or rows whose text the pattern misses) are labelled one by one.
    const reached = new Set(r.data.backfilled_ids || []);
    const labelledIds = new Set([body.tx_id, ...reached]);
    const rest = body.apply_scope === 'transaction' || (body.apply_scope === 'rule' && body.pattern) ? []
        : group.filter(x => x.id !== body.tx_id && !reached.has(x.id) && !x.type_id);
    let done = 0, error = null;
    for (const x of rest) {
        const { check_each_row: _, wordings: __, ...one } = body;   // the setting and the wordings went with the first
        const more = await send('POST', '/api/transactions/resolve', { ...one, tx_id: x.id, apply_scope: 'transaction', service_id: r.data.service_id ?? body.service_id });
        if (!more.ok) {
            error = more.data.error || 'One of the rows could not be labelled';
            if (!quiet) toast(error, { bad: true });
            break;
        }
        if (more.change) changes.push(more.change);
        labelledIds.add(x.id);
        done += 1;
    }
    const toCheck = (onScreen || (group.length ? group : [{ id: body.tx_id }])).filter(x => labelledIds.has(x.id));
    if (check && !error && toCheck.length) {
        const typeId = r.data.type_id ?? body.type_id;
        const checked = await send('POST', '/api/transactions/bulk-label', {
            rows: toCheck.map(x => ({ tx_id: x.id, type_id: typeId, book: check(x) || r.data.book })),
        });
        if (checked.change) changes.push(checked.change);
        if (!checked.ok) error = checked.data.error || 'The rows here could not be checked';
    }
    const others = done + (r.data.backfilled || 0);
    if (!quiet) {
        if (others) toast(`${plural(others, 'other row')} took the same label`);
        closeSheet(); rerender();
    }
    return { ok: !error, resolved: true, changes, labelled: labelledIds.size, labelledIds: [...labelledIds], book: r.data.book, past, error };
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
        const pattern = scope === 'transaction' ? '' : recWording('ww', row).trim();
        if (scope === 'rule' && !pattern) { toast('Say what text the rows contain', { bad: true }); return; }
        if (scope !== 'transaction' && recBlocks('ww')) { toast('That wording matches no row: check the wording', { bad: true }); return; }
        const each = scope === 'service_default' && val('cw-set') === 'each';
        const body = resolveBody(row, typeId, { merchant: name, scope, pattern, book: val('cw-book'), visible: step.suggestionVisible,
            each: scope === 'service_default' && val('cw-set') ? each : undefined });
        if (!each) {
            const res = await saveResolve(body, group);
            // Only this row, for a merchant fin holds (saveResolve names it): its book may now vary.
            if (res.resolved && scope === 'transaction' && body.service_id) askBookVaries(body.service_id);
            return;
        }
        // Check each row: the rows here are checked with the book shown, and
        // the toast's one Undo takes the whole save.
        const shown = val('cw-book') || null;   // read now: the offer's question may replace this sheet
        const res = await saveResolve(body, group, { quiet: true, check: () => shown });
        const type = step.r.typeById.get(typeId);
        if (res.resolved) closeSheet();
        savedToast(name, type?.display_name || type?.name || 'its type', res.book, res, group, { each });
        if (res.resolved) { rerender(); refreshFrame(); }
        return;
    }
    const body = { choice: choice.name };
    if (val('cw-type')) body.type_id = Number(val('cw-type'));
    if (val('cw-book')) body.book = val('cw-book');
    if (val('cw-account')) body.account_id = Number(val('cw-account'));
    if (val('cw-income')) body.income_kind_id = Number(val('cw-income'));
    const rows = untyped ? group : [row];
    if (rows.length === 1) {
        const r = await act('POST', `/api/review/${row.id}/label`, body, 'Labelled');
        if (r) { closeSheet(); rerender(); }
        return;
    }
    // One request a row; the toast's Undo takes back every row it labelled.
    let done = 0, failed = false;
    const changes = [];
    for (const x of rows) {
        const r = await send('POST', `/api/review/${x.id}/label`, body);
        if (!r.ok) { toast(`${done ? `${plural(done, 'row')} labelled; then: ` : ''}${r.data.error || 'that did not work'}`, { bad: true, undo: changes, ms: 9000 }); failed = true; break; }
        if (r.change) changes.push(r.change);
        done += 1;
    }
    if (done && !failed) toast(`${plural(done, 'row')} labelled: ${choice.label}.`, { undo: changes });
    if (done) { closeSheet(); rerender(); refreshFrame(); }
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

// "Yes, Shopping · Household": a row not checked yet, checked as it came in.
/** Accepting what fin offered is a check, not a choice (personal-ledger 24):
 *  bulk label with the row's own book and type, which marks both checked. */
function mixedYesBody(row) {
    return { rows: [{ tx_id: row.id, type_id: row.type_id, book: row.book }] };
}
ACT['mixed-yes'] = async el => {
    const row = await findRow(Number(el.dataset.tx));
    if (!row) return;
    const r = await act('POST', '/api/transactions/bulk-label', mixedYesBody(row), `Checked: ${row.display_type || 'its type'} · ${row.book}`);
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
            <a class="btn" href="#/books/import">Import a fixed file</a>` });
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
/** What Enter a figure asks for on an account. A loan with hire-purchase
 *  terms is owed principal: its statement's total of remaining instalments
 *  is not what to type (loan_terms.py). */
function figureLabel(a) {
    const sign = SIGNS[a.currency] || a.currency;
    if (OWED_KINDS.includes(a.type)) {
        return a.terms ? `Principal owed now, in ${sign} (not the statement's total of remaining instalments)` : `What is owed now, in ${sign}`;
    }
    // A shared account's figure is the household's part (shared.py), not its statement balance.
    if (a.shared) return `Our part now, in ${sign} (the household's money in it, not the account's balance)`;
    return a.type === 'holding' ? `What it is worth now, in ${sign}` : `The balance now, in ${sign}`;
}
/** What Enter a figure says of an account with no figure yet. A loan with
 *  hire-purchase terms already has its interest worked out from them; it
 *  joins net worth with its first figure (loan_interest, balance_sheet). */
function figureEmptyLine(a) {
    if (OWED_KINDS.includes(a.type) && a.terms) {
        return 'No figure yet. Its interest is worked out from its terms; it joins net worth when its first statement comes in, or when you enter the principal owed.';
    }
    if (a.shared) return 'No figure yet: our part is left out of net worth until you enter it. Date it on the day it was true; our own rows after it carry it forward.';
    return 'No figure yet: it is left out of net worth until it has one.';
}
/** The dialog's closing line. On a loan with terms the next statement import
 *  checks every figure against their schedule and stops if one does not fit
 *  (loan_interest.misfit), so "nothing checks it" is not said there. */
function figureCheckLine(a) {
    if (OWED_KINDS.includes(a.type) && a.terms) {
        return `It shows as “your figure” with its date, and turns stale after ${STALE_DAYS} days. The next statement import checks it against the terms’ schedule, and stops if it does not fit.`;
    }
    return `A figure you enter is the fact: nothing checks it. It shows as “your figure” with its date, and turns stale after ${STALE_DAYS} days.`;
}
async function openFigure(accountId) {
    const [r, sheet] = await Promise.all([refs(), sheetFor(currentMonth()).catch(() => null)]);
    const takes = r.accounts.filter(a => a.takes_a_figure && a.status !== 'archived');
    const named = takes.find(a => a.id === accountId);
    const chosen = named || takes[0];
    if (!chosen) { toast('No account takes a figure: add a loan, a holding, a company or a person in Lists', { bad: true }); return; }
    const lines = sheet ? sheet.sections.flatMap(s => s.lines).filter(l => !l.counted_in) : [];
    const sign = a => SIGNS[a.currency] || a.currency;
    const owes = a => OWED_KINDS.includes(a.type);
    const target = a => {
        const l = lines.find(x => x.account_id === a.id);
        const ro = l && l.rests_on;
        const stale = ro && ro.source === 'supplied' && ro.age_days > STALE_DAYS;
        const now = l && l.balance_minor !== null && l.balance_minor !== undefined
            ? `<div class="fg-now">Now <b class="num${l.balance_minor < 0 ? ' neg' : ''}">${esc(money(l.balance_minor, l.currency))}</b> ${stale ? tag('stale', 'stale') : ''}</div>
                ${ro ? `<div class="small muted">${ro.source === 'supplied' ? 'your figure' : 'statement'} ${esc(day(ro.date))}, ${esc(plural(ro.age_days, 'day'))} old</div>` : ''}`
            : `<div class="small muted">${esc(figureEmptyLine(a))}</div>`;
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
        <label class="field"><span id="fg-label">${esc(figureLabel(chosen))}</span><input type="text" inputmode="decimal" id="fg-amount" placeholder="${esc(placeholder(chosen))}" aria-describedby="fg-hint"></label>
        <p class="hint" id="fg-hint">${esc(hint(chosen))}</p>
        <label class="field"><span>On</span><input type="date" id="fg-date" value="${todayIso()}"></label>
        <label class="field"><span>Note</span><input type="text" id="fg-note" placeholder="Where the figure came from"></label></div>
        <p class="small muted" id="fg-check">${esc(figureCheckLine(chosen))}</p>`,
    { eyebrow: 'Your figure', foot: '<button class="btn primary" data-act="figure-save">Save the figure</button>' });
    const current = () => r.accountById.get(Number($('#fg-account').value));
    $('#fg-account').addEventListener('change', () => {
        const a = current();
        $('#fg-target').innerHTML = target(a);
        $('#fg-label').textContent = figureLabel(a);
        $('#fg-amount').placeholder = placeholder(a);
        $('#fg-hint').textContent = hint(a, $('#fg-amount').value);
        $('#fg-check').textContent = figureCheckLine(a);
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
/** A company's balance as a short sum, the zero parts dropped (a person has
 *  no sheet line since the switch, so never a made_of):
 *  "opening S$ 15,000.00 + paid for it S$ 1,830.99 (23 rows) = S$ 16,830.99". */
function madeOfSum(line) {
    const m = line.made_of;
    if (!m) return '';
    const parts = [];
    if (m.opening_minor) parts.push(['', 'opening', m.opening_minor]);
    if (m.paid_for_minor) parts.push(['+', 'paid for it', m.paid_for_minor]);
    if (m.capital_minor) parts.push(['+', 'capital', m.capital_minor]);
    if (m.paid_back_minor) parts.push(['−', 'paid back', m.paid_back_minor]);
    if (!parts.length) return 'no money moved yet';
    if (parts.length === 1 && parts[0][0] === '') {
        return `opening ${money(m.opening_minor)}, nothing moved since`;
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
/** The sheet's sortable columns: each by its sortKey, as the sheet has always sorted. */
const SHEET_SORT = { value: { label: 'In S$', kind: 'number', of: l => sortKey(l, 'value') }, rests: { label: 'Rests on', kind: 'number', of: l => sortKey(l, 'rests') },
    check: { label: 'Check', kind: 'number', of: l => sortKey(l, 'check') } };

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
    const sorted = lines => sortRows('sheet', lines, SHEET_SORT);
    const shown = line => !S.needsLook || lineNeedsLook(line, refusedBy);
    const sortBtn = key => sortHead('sheet', key, SHEET_SORT);
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
                ${sortPicker('sheet', SHEET_SORT, { own: 'Sheet order', cls: 'sheet-sort', choices: [['rests', 'Rests on: oldest first'], ['check', 'Check: worst first']] })}</div></div>
        <div class="table-shell sheet-table"><table class="t">
            <thead><tr><th style="min-width:170px">Account</th><th class="r">In its currency</th><th class="r">${sortBtn('value')}</th><th class="tick-cell"><span class="sr-only">Tick</span></th><th>${sortBtn('rests')}</th><th>${sortBtn('check')}</th><th>Margin note</th></tr></thead>
            <tbody>${table || '<tr><td colspan="7" class="empty">Nothing needs a look.</td></tr>'}</tbody>
            ${S.needsLook ? '' : `<tfoot><tr class="total"><td>Net worth</td><td></td><td class="r num">${esc(money(sheet.net_worth_minor, 'SGD'))}</td><td colspan="4"></td></tr></tfoot>`}</table></div>
        <div class="sheet-lines">${cards || '<p class="card-empty">Nothing needs a look.</p>'}
            ${S.needsLook ? '' : `<div class="line-total line-total--worth"><b>Net worth</b><b class="num">${esc(money(sheet.net_worth_minor, 'SGD'))}</b></div>`}</div>
        ${tickKey()}
        ${cc && cc.minor ? `<p class="card-foot">Currency change since ${esc(day(cc.from))}: <b class="num">${esc(money(cc.minor, 'SGD', { signed: true }))}</b></p>` : ''}
        ${cc && cc.minor === null && cc.note ? `<p class="card-foot">${tag('nofig', 'currency change not worked out')} ${esc(dateWords(cc.note))}
            ${[...new Set((cc.lines || []).map(l => l.currency).filter(Boolean))].map(c => `<button class="btn sm" data-act="rate" data-currency="${esc(c)}" data-date="${esc(rateDay(sheet.as_at))}">${esc(c)} rate</button>`).join(' ')}</p>` : ''}
    </section>${window.expectBooksSections?.(sheet) || ''}
    ${monthCheckHTML(sheet)}`;
}
ACT['needs-look'] = el => { S.needsLook = el.dataset.on === '1'; rerender(); };

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

const KIND_WORDS = { bank: 'Bank account', card: 'Card', loan: 'Loan', holding: 'Holding', company: 'Company', person: 'Person', untracked: 'Untracked account' };
/** What a row is labelled, as an account's rows show it. */
function acctRowLabel(row) {
    const flowWord = { review: tag('nofig', 'waiting'), transfer: '<span class="muted">transfer</span>', payment: '<span class="muted">card payment</span>', movement: `<span class="muted">movement${row.other_side_name ? ' · ' + esc(row.other_side_name) : ''}</span>`, income: '<span class="muted">income</span>', not_ours: '<span class="muted">not ours</span>' }[row.flow_type];
    return flowWord || (row.display_type ? esc(row.display_type) : tag('nofig', 'no type'));
}
/** One of an account's rows: money in and money out in two plain columns. */
function acctRowTr(row) {
    const minor = toMinor(row.amount_sgd, row.currency);
    const cur = row.currency || 'SGD';
    const struck = row.counted_minor != null && row.counted_minor !== row.amount_minor;
    const inCell = minor !== null && minor < 0 ? (struck ? struckAmount(row) : esc(money(-minor, cur))) : '';
    const outCell = minor !== null && minor >= 0 ? (struck ? struckAmount(row) : esc(money(minor, cur))) : '';
    return `<tr class="clickable" data-act="row" data-tx="${row.id}">
        <td class="num row-date">${esc(day(row.date, { year: false }))}</td>
        <td class="row-desc">${esc(row.description)}${rowMark(row.id)}${window.expectRowPill?.(row) || ''}${row.notes ? ` <span class="muted small">· ${esc(row.notes)}</span>` : ''}${row.is_one_off ? ' <span class="tag yours">one-off</span>' : ''}${row.book && row.book !== 'Household' ? ` <span class="small muted">· ${esc(row.book)}</span>` : ''}</td>
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

/** An account's tie lines, sortable. One account is one currency, so its own figures stand in for S$. */
const tieWorked = t => t.opening_minor !== null && t.opening_minor !== undefined;
const TIE_SORT = {
    date: { label: 'Statement', kind: 'date', of: t => t.date },
    opening: { label: 'Opening', kind: 'money', of: t => tieWorked(t) ? t.opening_minor : null },
    in: { label: '+ money in', kind: 'money', of: t => tieWorked(t) ? t.in_minor || 0 : null },
    out: { label: '− money out', kind: 'money', of: t => tieWorked(t) ? t.out_minor || 0 : null },
    made: { label: '= works out to', kind: 'money', of: t => tieWorked(t) ? t.opening_minor + (t.in_minor || 0) - (t.out_minor || 0) : null },
    says: { label: 'Statement says', kind: 'money', of: t => t.closing_minor },
    check: { label: 'Check', kind: 'text', of: t => t.status },
};
/** An account's rows sort on the server (a page at a time). Money in and money out are each
 *  their own server column, so the rows on the other side (an empty cell) come last both ways. */
const ACCT_ROW_SORT = {
    date: { label: 'Date', kind: 'date' }, description: { label: 'Description', kind: 'text' }, type: { label: 'Labelled', kind: 'text' },
    in: { label: 'Money in', kind: 'money', server: 'amount_in' }, out: { label: 'Money out', kind: 'money', server: 'amount_out' },
};
const accountPage = { page: 1, month: null };
SORT_REDRAW['acct-rows'] = () => { accountPage.page = 1; rerender(); };
async function viewAccount(id) {
    // The page of rows needs none of the other reads: all six go in one round.
    if (accountPage.id !== id) { accountPage.id = id; accountPage.page = 1; accountPage.month = null; }
    const order = sortOf('acct-rows').key ? `&${new URLSearchParams(sortServer('acct-rows', ACCT_ROW_SORT))}` : '';
    const [r, sheet, ties, anchorsList, refused, rows] = await Promise.all([
        refs(), sheetFor(S.month), get(`/api/accounts/${id}/ties`), get(`/api/anchors?account_id=${id}`), get(`/api/statements/refused?account_id=${id}`),
        get(`/api/transactions?account_id=${id}&per_page=50&page=${accountPage.page}${accountPage.month ? `&month=${accountPage.month}` : ''}${order}`),
    ]);
    const acct = r.accountById.get(id);
    if (!acct) throw new Error('No such account');
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
        const tieRows = sortRows('acct-ties', ties.ties, TIE_SORT).map(t => {
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
            ${tieRows ? `${sortPicker('acct-ties', TIE_SORT, { own: 'Newest first' })}<div class="table-shell"><table class="t cells ties-table"><thead><tr>${Object.keys(TIE_SORT).map(k =>
                `<th${TIE_SORT[k].kind === 'money' ? ' class="r"' : ''}>${sortHead('acct-ties', k, TIE_SORT)}</th>`).join('')}</tr></thead><tbody>${tieRows}</tbody></table></div>`
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
    // A person is no balance since the switch (personal-ledger 23): their page is what is open with them.
    if (acct.type === 'person') return personAccountPage(acct, figures, supplied.length);

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
    // Sorted, the rows are one list: a tie line's heading groups only rows in date order.
    let lastKey = null;
    const rowList = rows.transactions.map(row => {
        const g = groupOf(row);
        let headRow = '';
        if (!order && g.key !== lastKey) {
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
    afterRender(() => window.mountAccountTrend?.());

    return `${booksNav('sheet')}
    <div class="page-head"><div><a class="link small back-link" href="#/books">← Balance sheet</a><span class="eyebrow">Books · ${esc(KIND_WORDS[acct.type] || acct.type)}</span><h1>${esc(acct.name)}${accountMark(id)}</h1>
        <p>${esc(acct.type)} · ${esc(acct.owner)} · kept in ${esc(cur)}${acct.status === 'archived' ? ' · archived' : ''}</p>${window.folioSlot?.('account', id) || ''}</div>
        <div class="row page-head__end">${acct.takes_a_figure ? `<button class="btn" data-act="figure" data-account="${id}">${icon('pencil-simple')}Enter a figure</button>` : ''}${monthPicker()}</div></div>
    <div class="acct-top${tieCard ? '' : ' acct-top--one'}">${hero}${tieCard}</div>
    ${refusedHTML ? `<section class="card"><div class="section-header"><h2>Refused at upload</h2><span class="section-header__aside">a statement that does not tie is kept out whole</span></div>${refusedHTML}</section>` : ''}
    ${figures ? `<section class="card"><div class="section-header"><h2>Your figures</h2><span class="section-header__aside">${plural(supplied.length, 'figure')} · each the fact</span></div><ul class="fig-list">${figures}</ul></section>` : ''}
    <section class="card"><div class="section-header"><h2>Rows</h2><span class="section-header__aside">${plural(rows.total, 'row')} · tap a row for its note, one-off and history · <a class="link" href="#/books/rows?account=${id}&month=all">Open in Rows</a></span></div>
        ${sortPicker('acct-rows', ACCT_ROW_SORT, { own: 'Newest first' })}<div class="table-shell"><table class="t rows acct-rows"><thead><tr>${Object.keys(ACCT_ROW_SORT).map(k =>
            `<th${ACCT_ROW_SORT[k].kind === 'money' ? ' class="r"' : ''}>${sortHead('acct-rows', k, ACCT_ROW_SORT)}</th>`).join('')}</tr></thead><tbody>${rowList || '<tr><td colspan="5" class="empty">No rows.</td></tr>'}</tbody></table></div>
        ${pager(rows, 'account-page', !!order)}</section>`;
}
ACT['account-page'] = el => { accountPage.page = Number(el.dataset.page); rerender(); window.scrollTo(0, 0); };
/** A person account's page (personal-ledger 23, the switch): a person is no
 *  balance on Books, so in place of the balance and its trend it lists what is
 *  open with them, expected back and to pay back, oldest first, each opening its
 *  expectation's page. Their figures stay listed, to correct one if need be. */
async function personAccountPage(acct, figures, figureCount) {
    const open = await get(`/api/expectations?person=${acct.id}`);
    const items = open.items || [];
    const words = x => x.direction === 'pay' ? 'to pay back' : 'expected back';
    const title = x => x.row ? x.row.description : `Opening figure · ${x.opening ? x.opening.name : acct.name}`;
    const list = items.map(x => `<li class="fig-row"><span class="fig-row__date">${esc(day(x.day))}</span>
        <span class="fig-row__amt num">${esc(money(x.open_minor, x.currency))}${x.open_minor !== x.amount_minor ? ` of ${esc(money(x.amount_minor, x.currency))}` : ''}</span>
        <span class="fig-row__note"><a class="link" href="#/books/expectation/${Number(x.id)}">${esc(title(x))}</a> · ${words(x)}</span>
        <span class="fig-row__acts"></span></li>`).join('');
    return `${booksNav('sheet')}
    <div class="page-head"><div><a class="link small back-link" href="#/books">← Balance sheet</a><span class="eyebrow">Books · ${esc(KIND_WORDS[acct.type] || acct.type)}</span><h1>${esc(acct.name)}${accountMark(acct.id)}</h1>
        <p>A person is no balance on Books: what is lent or owed is what is expected back and to pay back, named for them.</p>${window.folioSlot?.('account', acct.id) || ''}</div>
        <div class="row page-head__end">${monthPicker()}</div></div>
    <section class="card"><div class="section-header"><h2>Open with ${esc(acct.name)}</h2><span class="section-header__aside">${plural(items.length, 'item')} · oldest first</span></div>
        ${list ? `<ul class="fig-list">${list}</ul>` : `<p class="card-empty">Nothing is open with ${esc(acct.name)}.</p>`}</section>
    ${figures ? `<section class="card"><div class="section-header"><h2>Your figures</h2><span class="section-header__aside">${plural(figureCount, 'figure')} · no longer read by Books</span></div><ul class="fig-list">${figures}</ul></section>` : ''}`;
}
function pager(list, actName, sorted = false) {
    if (!list.pages || list.pages <= 1) return '';
    // Newer and Older hold only in the rows' own order, newest first; sorted, the pages are just before and after.
    return `<div class="pager"><span class="small muted">page ${list.page} of ${list.pages}</span>
        <button class="btn sm" data-act="${actName}" data-page="${list.page - 1}"${list.page <= 1 ? ' disabled' : ''}>${sorted ? 'Previous' : 'Newer'}</button>
        <button class="btn sm" data-act="${actName}" data-page="${list.page + 1}"${list.page >= list.pages ? ' disabled' : ''}>${sorted ? 'Next' : 'Older'}</button></div>`;
}
/** A row of a Check each row merchant that nobody has checked yet: it counts
 *  in its usual book meanwhile, and waits in the Queue. */
function notCheckedTag(row) { return row.not_checked ? ` ${tag('notchecked', 'not checked')}` : ''; }
function rowTr(row, { account = true, book = false } = {}) {
    const minor = toMinor(row.amount_sgd, row.currency);
    const flowWord = { review: tag('nofig', 'waiting'), transfer: '<span class="muted small">transfer</span>', payment: '<span class="muted small">card payment</span>', movement: `<span class="muted small">movement${row.other_side_name ? ' · ' + esc(row.other_side_name) : ''}</span>`, income: '<span class="muted small">income</span>', not_ours: '<span class="muted small">not ours</span>' }[row.flow_type];
    const label = flowWord || (row.display_type ? esc(row.display_type) : tag('nofig', 'no type'));
    return `<tr class="clickable" data-act="row" data-tx="${row.id}">
        <td class="num">${esc(day(row.date, { year: false }))}</td>
        <td>${esc(row.description)}${rowMark(row.id)}${window.expectRowPill?.(row) || ''}${row.notes ? ` <span class="muted small">· ${esc(row.notes)}</span>` : ''}${row.is_one_off ? ' <span class="tag yours">one-off</span>' : ''}${account ? `<div class="small muted">${esc(row.account_name || '')}</div>` : ''}</td>
        <td>${label}${book || (row.book && row.book !== 'Household') ? ` <span class="small muted">· ${esc(row.book || '')}</span>` : ''}${notCheckedTag(row)}</td>
        <td class="r">${row.counted_minor != null && row.counted_minor !== row.amount_minor ? `${dirTag(minor < 0 ? 'in' : 'out')} ${struckAmount(row)}` : rowAmount(minor, row.currency)}</td></tr>`;
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
// with a total each, never one sum. The total is net of refunds: the signed
// sum of the month's rows, a refund counting in the month it arrives. S$
// accounts only; a rupee row is named beside the figure, not added to it.
// ---------------------------------------------------------------------------

const charts = [];
/** What a row counts in a spending figure, in minor units of its currency: the payload's counted_minor, which the
 *  server sets to the row's own part once expect back is switched on (its amount less what is expected back on it)
 *  and to its amount until then. A payload without the field counts the amount, as before. Never worked out here. */
function countedMinor(row) {
    const c = row.currency || 'SGD';
    return Number.isInteger(row.counted_minor) ? row.counted_minor : toMinor(row.amount_sgd, c);
}
/** Net per currency: the signed sum of what the rows count, money out positive and a refund negative. */
function totalsByCurrency(rows) {
    const by = {};
    rows.forEach(row => { const c = row.currency || 'SGD'; by[c] = (by[c] || 0) + countedMinor(row); });
    return by;
}
/** What rows count in S$ on the one path (valuation.py): each row's counted part at the rate of its day. */
/** A row's counted part in S$ at the rate of its day, or null when no saved rate values it. */
function sgdOf(row) {
    return Number.isInteger(row.counted_sgd_minor) ? row.counted_sgd_minor
        : (row.currency || 'SGD') === 'SGD' ? countedMinor(row) : null;
}
function totalSgd(rows) {
    return rows.reduce((a, row) => a + (sgdOf(row) ?? 0), 0);
}
/** Per currency, what rows in another currency count with no rate to put them in S$ by. */
function unvaluedBy(rows) {
    return totalsByCurrency(rows.filter(row => (row.currency || 'SGD') !== 'SGD' && !Number.isInteger(row.counted_sgd_minor)));
}
function totalsText(by) {
    const parts = Object.entries(by).map(([c, m]) => money(m, c));
    return parts.length ? parts.join(' and ') : money(0, 'SGD');
}
/** Rows grouped by `keyOf`, each group's net in S$ first: a group netting below zero sorts last. */
function groupRows(rows, keyOf) {
    const groups = new Map();
    rows.forEach(row => {
        const k = keyOf(row);
        if (!groups.has(k)) groups.set(k, []);
        groups.get(k).push(row);
    });
    return [...groups.entries()].map(([name, list]) => { const by = totalsByCurrency(list); return { name, list, by, sgd: totalSgd(list) }; })
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
    const label = row.display_type ? (row.type_id ? pageLink('type', row.type_id, row.display_type) : esc(row.display_type)) : tag('nofig', 'no type');
    const what = row.service_id ? pageLink('merchant', row.service_id, row.description) : esc(row.description);
    const account = row.account_id ? pageLink('account', row.account_id, row.account_name || '') : esc(row.account_name || '');
    const struck = row.counted_minor != null && row.counted_minor !== row.amount_minor;
    const amount = minor < 0
        ? `<span class="sl-refund">${icon('arrow-circle-down')}refund</span> ${struck ? struckAmount(row) : `<span class="num sl-back">${esc(money(-minor, row.currency))} back</span>`}`
        : struck ? struckAmount(row) : `<span class="num">${esc(money(minor, row.currency))}</span>`;
    return `<tr class="clickable" data-act="row" data-tx="${row.id}">
        <td class="num">${esc(day(row.date, { year: false }))}</td>
        <td>${what}${rowMark(row.id)}${window.expectRowPill?.(row) || ''}${row.notes ? ` <span class="muted small">· ${esc(row.notes)}</span>` : ''}${row.is_one_off ? ' <span class="tag yours">one-off</span>' : ''}<div class="small muted">${account}</div></td>
        <td>${label}${notCheckedTag(row)}</td>
        <td class="r">${amount}</td></tr>`;
}

/** The name cell of a By merchant / By type row: a link to its page where it has one (a merchant by its id, a type by its id,
 *  or, folded into its parent, the parent's), plain text where it has none (a row with no merchant, "No type"). */
function spendGroupNameCell(g, view, fold, types) {
    if (view !== 'type') {
        const row = g.list.find(x => x.service_id && x.service_name === g.name);
        return row ? pageLink('merchant', row.service_id, g.name) : esc(g.name);
    }
    if (g.name === 'No type') return tag('nofig', 'No type');
    const id = fold ? types.find(t => t.name === g.name && !t.parent_id)?.id : g.list[0]?.type_id;
    return id ? pageLink('type', id, g.name) : esc(g.name);
}

/** Spending's Every row columns. Amount sorts by the row's counted S$ part; a row in
 *  another currency with no saved rate for its month has none, and goes last. */
const SPEND_ROW_SORT = { date: { label: 'Date', kind: 'date', of: x => x.date }, description: { label: 'Description', kind: 'text', of: x => x.description },
    type: { label: 'Type', kind: 'text', of: x => x.display_type }, amount: { label: 'Amount', kind: 'money', of: sgdOf } };
async function viewSpending() {
    const sp = spendState();
    const r = await refs();
    const months = monthsBack(S.month, 12);
    const span = sp.span === 'year' ? months : [S.month];
    const household = r.books[0].name;
    const books = sp.book === 'all' ? r.books.map(b => b.name) : [sp.book];
    // The lists keep hiding the merchants hidden from expense views; the tiles,
    // the aside and the chart count them (include_hidden), as stat-cards does.
    const qs = (book, counted = false) => {
        const p = new URLSearchParams({ book, start: months[0] + '-01', end: monthEnd(S.month), expense_only: 'true', sort: 'date', sort_dir: 'desc' });
        if (counted) p.set('include_hidden', 'true');
        if (sp.search) p.set('search', sp.search);
        if (sp.types.length) p.set('types', sp.types.join(','));
        if (sp.account) p.set('account_id', sp.account);
        if (!sp.oneOffs) p.set('exclude_one_off', 'true');
        return p.toString();
    };
    // Every page is read: a total is never one page's sum.
    const [lists, counts, coverage] = await Promise.all([
        Promise.all(books.map(b => getAllRows(qs(b)))),
        Promise.all(books.map(b => getAllRows(qs(b, true)))),
        get(`/api/statements/coverage?months=${monthsSince(months[0])}`).catch(() => null),
    ]);
    const perBook = books.map((book, i) => {
        const year = lists[i].transactions;           // what the lists and tables show
        const counted = counts[i].transactions;       // what the figures count
        const inSpan = rows => rows.filter(row => span.includes(row.date.slice(0, 7)));
        const net = inSpan(counted);
        const spent = net.filter(row => !isRefund(row));
        const back = net.filter(isRefund);
        const others = [...new Set(counted.map(row => row.currency || 'SGD').filter(c => c !== 'SGD'))];
        return { book, year, counted, all: inSpan(year), net, spent, back, by: totalsByCurrency(net), spentBy: totalsByCurrency(spent),
            backBy: totalsByCurrency(back), sgdNet: totalSgd(net), sgdSpent: totalSgd(spent), sgdBack: totalSgd(back),
            unvalued: unvaluedBy(net), short: shownOf(counts[i]) || shownOf(lists[i]), others };
    });
    ACT.__rows = new Map(perBook.flatMap(b => b.all).map(x => [x.id, x]));
    const spanWords = span.length === 1 ? `${monthName(S.month)}${S.month === currentMonth() ? ' so far' : ''}` : `12 months to ${monthName(S.month, { short: true })}`;

    // P1.3: a month with nothing in it says why, and where the latest rows are.
    let emptyNote = '';
    if (sp.span !== 'year' && perBook.every(b => !b.net.length)) {
        const latest = perBook.flatMap(b => b.counted).map(x => x.date).sort().pop();
        const filtered = activeSpendFilters(sp, r).length;
        emptyNote = `<div class="notice sl-empty-month">${icon('calendar-blank')}<div class="grow"><b>Nothing in ${esc(monthName(S.month))}${S.month === currentMonth() ? ' yet' : ''}.</b>
            ${latest ? `The latest rows are from ${esc(day(latest, { year: latest.slice(0, 4) !== S.month.slice(0, 4) }))}${S.month === currentMonth() ? `; ${esc(LONG_MONTHS[Number(S.month.slice(5)) - 1])}’s statements are not in` : ''}.` : filtered ? 'Nothing matches the filters in the 12 months either.' : 'No statement for it is in.'}</div>
            ${latest ? `<button class="btn" data-act="spend-month" data-m="${latest.slice(0, 7)}">Look at ${esc(LONG_MONTHS[Number(latest.slice(5, 7)) - 1])}</button>` : ''}</div>`;
    }

    // P1.1, P1.2: one tile per book; its figure is net of refunds in S$, a row in
    // another currency at the rate of its own day (valuation.py, RUL-022).
    const otherNote = b => {
        const left = Object.entries(b.unvalued).filter(([, m]) => m).map(([c, m]) => money(m, c));
        const valued = Object.entries(b.by).filter(([c, m]) => c !== 'SGD' && m - (b.unvalued[c] || 0)).map(([c, m]) => money(m - (b.unvalued[c] || 0), c));
        return [valued.length ? `<p class="small muted sl-other">Includes ${esc(valued.join(' and '))}, each row at the saved rate of its day.</p>` : '',
            left.length ? `<p class="small muted sl-other">${esc(left.join(' and '))} has no saved rate for its month and is not in this figure.</p>` : ''].join('');
    };
    const tiles = `<div class="stat-tiles sl-tiles">${perBook.map(b => {
        const isHouse = b.book === household;
        return `<div class="stat-tile${isHouse ? '' : ' hatch'}">
            <div class="sl-tile-head"><span class="stat-tile__label">${isHouse ? 'Household spending' : `${esc(b.book)} · its costs`}</span>${isHouse ? '' : '<span class="outside-tag">not household</span>'}</div>
            <div class="stat-tile__figure">${esc(money(b.sgdNet, 'SGD'))}</div>
            <div class="stat-tile__sub">${plural(b.spent.length, 'row')} out · ${esc(spanWords)}</div>
            ${b.sgdBack ? `<div class="sl-back-line"><span class="delta-chip delta-chip--up">${icon('arrow-circle-down')}after ${esc(money(-b.sgdBack, 'SGD'))} refunds</span></div>` : ''}
            ${otherNote(b)}
            ${b.short ? `<p class="small sl-short">${icon('warning')}Short: too many rows to read at once (${esc(b.short)} over 12 months). Narrow the filters.</p>` : ''}</div>`;
    }).join('')}</div>
        <p class="card-foot">${perBook.length > 1 ? 'Each book has its own total. They are never added together. ' : ''}Net of refunds: a refund counts in the month it arrives. In S$, a row in another currency at the saved rate of its day.</p>`;

    // P3.1: a month with no statement in for this book's accounts is marked, not drawn as nothing spent.
    const missingFor = b => {
        if (!coverage) return new Set();
        const ids = new Set(b.counted.map(x => x.account_id).filter(id => coverage.matrix[id]));
        const out = new Set();
        if (!ids.size) return out;
        months.forEach(m => {
            if (m >= currentMonth()) return;
            if (b.counted.some(x => x.date.startsWith(m))) return;
            if ([...ids].some(id => coverage.matrix[id][m] && !coverage.matrix[id][m].imported)) out.add(m);
        });
        return out;
    };
    const fold = sp.fold;
    const typeKey = row => (fold ? (row.parent_type || row.type) : row.display_type) || 'No type';
    const body = perBook.map(b => {
        // P1.4: a book with no costs in the 12 months folds to one line.
        if (!b.counted.length) {
            return `<section class="card sl-folded"><h2>${esc(b.book)}</h2><p>No costs in the 12 months to ${esc(monthName(S.month, { short: true }))}${activeSpendFilters(sp, r).length ? ' that match the filters' : ''}, so there is no chart.</p>
                <a class="link" href="#/books">Look at ${esc(b.book)} on the balance sheet</a></section>`;
        }
        b.missing = missingFor(b);
        let content;
        if (sp.view === 'flat') {
            const list = b.all;
            const head = k => sortHead('spend-rows', k, SPEND_ROW_SORT);
            content = list.length ? `${sortPicker('spend-rows', SPEND_ROW_SORT, { own: 'Newest first' })}<div class="table-wrap"><table class="t rows sl-rows"><thead><tr><th>${head('date')}</th><th>${head('description')}</th><th>${head('type')}</th><th class="r">${head('amount')}</th></tr></thead>
                <tbody>${sortRows('spend-rows', list, SPEND_ROW_SORT).slice(0, 300).map(spendRowTr).join('')}</tbody></table></div>
                <p class="card-foot">Every row here is money out unless it says refund.${list.length > 300 ? ` The first 300 of ${list.length} rows; narrow the filters to see the rest.` : ''}</p>`
                : `<p class="card-empty">No rows in ${esc(spanWords)}.</p>`;
        } else {
            const keyOf = sp.view === 'type' ? typeKey : row => row.service_name || row.description;
            const groups = groupRows(b.all, keyOf);
            const noun = sp.view === 'type' ? 'Type' : 'Merchant';
            const nameCell = g => spendGroupNameCell(g, sp.view, fold, r.types);
            const whole = b.sgdNet;   // the tile's figure: every share is of it
            // Net and Share sort by the S$ part, which every share is of.
            const cols = { name: { label: noun, kind: 'text', of: g => g.name }, rows: { label: 'Rows', kind: 'number', of: g => g.list.length },
                net: { label: 'Net', kind: 'money', of: g => g.sgd }, share: { label: 'Share', kind: 'money', of: g => g.sgd } };
            const head = k => sortHead('spend-groups', k, cols);
            content = groups.length ? `<div class="table-wrap"><table class="t sl-groups"><thead><tr><th>${head('name')}</th><th class="r">${head('rows')}</th><th class="r">${head('net')}</th><th class="r desk-only">${head('share')}</th></tr></thead><tbody>
                ${sortRows('spend-groups', groups, cols).slice(0, 60).map(g => `<tr class="clickable" data-act="spend-drill" data-key="${esc(g.name)}" data-view="${sp.view}"><td>${nameCell(g)}</td><td class="r num">${g.list.length}</td>
                    <td class="r num">${esc(totalsText(g.by))}</td><td class="r num desk-only">${g.sgd > 0 && whole > 0 ? pct(g.sgd, whole) : '—'}</td></tr>`).join('')}
                ${groups.length > 60 ? `<tr><td colspan="4" class="muted">…and ${groups.length - 60} more</td></tr>` : ''}
                </tbody></table></div>`
                : `<p class="card-empty">No rows in ${esc(spanWords)}.</p>`;
        }
        const missingWords = b.missing.size ? ` A hatched month has no statement in for some of this book’s accounts; <a class="link" href="#/books/import">import it</a>.` : '';
        return `<section class="card"><div class="section-header"><h2>${esc(b.book)}</h2>
                <span class="section-header__aside">net <b class="num">${esc(money(b.sgdNet, 'SGD'))}</b> · spent <b class="num">${esc(money(b.sgdSpent, 'SGD'))}</b>${b.sgdBack ? ` · refunded <b class="num">${esc(money(-b.sgdBack, 'SGD'))}</b>` : ''}</span></div>
            <div class="chart-box small sl-chart"><canvas id="chart-${esc(b.book)}" aria-label="${esc(b.book)}, net spending by month"></canvas></div>
            <p class="small muted sl-chart-note">Net of refunds by month, 12 months to ${esc(monthName(S.month, { short: true }))}, on its own scale; a month with more refunded than spent falls below the axis. Tap a month to look at it.
                In S$${b.others.length ? `; ${esc(b.others.map(c => SIGNS[c] || c).join(' and '))} rows at the saved rate of their day, and a row with no rate for its month is left out` : ''}.${missingWords}</p>
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
            <button type="button" class="btn" data-act="spend-filters" aria-haspopup="dialog">${icon('funnel')}Filters${active.length ? ` <span class="count">${active.length}</span>` : ''}</button>
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
        foot: '<button type="button" class="btn" data-act="spend-clear">Clear every filter</button><button type="button" class="btn" data-act="close-sheet">Done</button>' });
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
        // Per month in S$, each row at the rate of its day (sgdOf; one with no rate left out), hidden merchants counted: spent, refunded and their net.
        const monthly = months.map(m => {
            const inMonth = b.counted.filter(row => row.date.startsWith(m) && sgdOf(row) != null);
            const spentMinor = inMonth.filter(row => !isRefund(row)).reduce((a, row) => a + sgdOf(row), 0);
            const backMinor = -inMonth.filter(isRefund).reduce((a, row) => a + sgdOf(row), 0);
            return { spent: spentMinor, back: backMinor, net: spentMinor - backMinor };
        });
        const sums = monthly.map(x => x.net / 100);
        const top = Math.max(...sums, 1);
        const axisWords = v => `${v < 0 ? '−' : ''}S$ ${Math.abs(v) >= 1000 ? (Math.abs(v) / 1000) + 'k' : Math.abs(v)}`;
        const fill = tk.books[i % tk.books.length];
        const label = m => MONTHS[Number(m.slice(5)) - 1];
        charts.push(new Chart(canvas, {
            type: 'bar',
            data: { labels: months.map(m => missing.has(m) ? [label(m), narrow ? '⧗' : '⧗ missing'] : label(m)), datasets: [
                { label: `${b.book}, net of refunds`, data: sums,
                    backgroundColor: months.map(m => m === S.month ? tk.current : fill), borderRadius: 4, barPercentage: 0.64, categoryPercentage: 1 },
                { label: 'no statement in', data: months.map(m => missing.has(m) ? top : null),
                    backgroundColor: hatchPattern(tk.base, tk.surface), borderColor: tk.base, borderWidth: 1, borderRadius: 4, barPercentage: 0.64, categoryPercentage: 1 },
            ] },
            options: {
                responsive: true, maintainAspectRatio: false, animation: false,
                plugins: { legend: { display: false }, tooltip: { callbacks: { label: c => {
                    if (c.datasetIndex === 1) return 'No statement in yet for this month: import it';
                    const x = monthly[c.dataIndex];
                    return [`Spent ${money(x.spent, 'SGD')}`, `Refunded ${money(x.back, 'SGD')}`, `Net ${money(x.net, 'SGD')}`];
                } } } },
                scales: {
                    x: { stacked: true, grid: { display: false }, border: { color: tk.base },
                        ticks: { font: { family: tk.mono, size: narrow ? 9 : 10 }, maxRotation: 0, autoSkip: false, color: c => missing.has(months[c.index]) ? warn : tk.ink } },
                    y: { stacked: true, beginAtZero: true, border: { display: false }, grid: { color: tk.grid, lineWidth: 1 }, ticks: { color: tk.ink, font: { family: tk.mono, size: 10 }, maxTicksLimit: 5, callback: axisWords } },
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

/** How often, in the server's words (bills.py): "monthly", "half-yearly", "every 2 months". */
function billEvery(s) { return s.every || s.frequency; }
function billMissedTag(s, missed) {
    return `<button type="button" class="tag stale" data-act="bill-queue" data-id="${s.id}" title="Open it in the queue">${icon('hourglass')}no payment seen for ${esc(day(missed.due, { year: missed.due.slice(0, 4) !== todayIso().slice(0, 4) }))}</button>`;
}
/** Late, but the paying account's statement for those days is not in yet: not missed. */
function billAwaitingTag(s) {
    return tag('aside', 'awaiting statement', { title: billWaitsOn(s) });
}
/** What an awaiting bill waits for, in words: the oldest due day not checked yet
 *  (the server's waiting_due) and the statement that would show it (waiting_on). */
function billWaitsOn(s, { named = true } = {}) {
    return `${day(s.waiting_due || s.due)} not checked yet; waiting on ${named ? `${s.account_name || 'its account'}’s` : 'its'} ${s.waiting_on ? monthName(s.waiting_on) + ' ' : ''}statement`;
}
/** A bill held in another currency than the account that pays it. */
function billCurrencyTag(s) {
    return tag('nofig', `${s.currency} bill, ${s.account_currency} account`, { title: 'A bill is in its paying account’s currency: open it to change it' });
}
/** Bills' sortable columns. Amount sorts by its S$ figure: a bill in another currency has none on it, so goes last. */
const BILL_SORT = {
    name: { label: 'Bill', kind: 'text', of: x => x.s.service_name || x.s.match_pattern },
    amount: { label: 'Amount', kind: 'money', of: x => (x.s.currency || 'SGD') === 'SGD' ? toMinor(x.s.amount, 'SGD') : null },
    often: { label: 'How often', kind: 'text', of: x => billEvery(x.s) },
    month: { label: 'A month', kind: 'money', of: x => x.s.monthly_sgd == null ? null : toMinor(x.s.monthly_sgd, 'SGD') },
    from: { label: 'Paid from', kind: 'text', of: x => x.s.account_name },
    last: { label: 'Last paid', kind: 'date', of: x => x.s.tx_last_paid || x.s.last_paid },
    next: { label: 'Next', kind: 'date', of: x => x.s.status === 'active' ? x.s.computed_renewal : null },
};
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
        // Missed, awaiting its statement, or paused or stopped; and a bill in another currency than its account's.
        const lead = missed ? billMissedTag(s, missed) : s.state === 'awaiting_statement' ? billAwaitingTag(s)
            : s.state === 'unknown_cadence' ? tag('nofig', 'how often unknown')
            : s.status !== 'active' ? tag('aside', s.status === 'deactivated' ? 'stopped' : s.status) : '';
        const state = [lead, s.currency_mismatch ? billCurrencyTag(s) : ''].filter(Boolean).join(' ');
        // On a phone the tags sit together, compact, on their own line under the bill's line.
        const tags = state ? `<span class="sl-tags">${state}</span>` : '';
        const sub = missed ? tags
            : `${esc(billEvery(s))}${s.status === 'active' ? ` · next ${esc(day(s.computed_renewal, { year: false }))}` : ''}${s.account_name ? ' · ' + esc(s.account_name) : ''} ${tags}`;
        return `<tr class="clickable${s.status === 'active' ? '' : ' sl-inactive'}" data-act="bill" data-id="${s.id}">
            <td class="sl-name"><b>${name}</b><div class="small muted">${esc(s.display_type || 'no type')}</div>${state ? `<div class="sl-state">${state}</div>` : ''}</td>
            <td class="r num sl-fig">${esc(money(toMinor(s.amount, cur), cur))}${s.is_variable ? '<div class="small muted">varies</div>' : ''}</td>
            <td>${esc(billEvery(s))}</td>
            <td class="r num">${s.monthly_sgd == null ? '—' : esc(money(toMinor(s.monthly_sgd, 'SGD'), 'SGD'))}</td>
            <td class="small">${esc(s.account_name || '')}</td>
            <td class="num">${esc(day(s.tx_last_paid || s.last_paid))}</td>
            <td class="num">${s.status === 'active' ? esc(day(s.computed_renewal)) : '—'}</td>
            <td class="sl-sub">${sub}</td></tr>`;
    };
    const groups = [...byBook.entries()].map(([book, list]) => {
        const active = list.filter(x => x.s.status === 'active');
        const monthly = active.reduce((a, x) => a + (x.s.monthly_sgd == null ? 0 : toMinor(x.s.monthly_sgd, 'SGD')), 0);
        const missed = list.filter(x => x.missed).sort((a, b) => a.missed.due.localeCompare(b.missed.due));
        const awaiting = list.filter(x => x.s.state === 'awaiting_statement').sort((a, b) => String(a.s.due).localeCompare(String(b.s.due)));
        const paid = list.filter(x => !x.missed && x.s.state !== 'awaiting_statement').sort((a, b) => (statusOrder[a.s.status] ?? 3) - (statusOrder[b.s.status] ?? 3)
            || String(a.s.computed_renewal || '').localeCompare(String(b.s.computed_renewal || '')));
        const counted = [missed.length ? `${missed.length} of ${list.length} not seen` : '', awaiting.length ? `${awaiting.length} awaiting` : ''].filter(Boolean).join(' · ');
        const head = `<tr class="section sl-book"><td colspan="5" class="sl-name">${esc(book)}</td>
            <td colspan="3" class="r sl-fig"><span class="sl-amt">${esc(money(monthly, 'SGD'))} a month</span> <span class="sep" aria-hidden="true">·</span> <span class="sl-counts">${list.length ? counted || plural(list.length, 'bill') : 'no bills'}</span></td></tr>`;
        // Sorted, a book's bills are one list (each still says its state); else missed first, then those
        // awaiting their account's statement, then those paid as expected.
        if (sortOf('bills').key) return head + sortRows('bills', list, BILL_SORT).map(row).join('');
        const part = (words, rows, headed) => rows.length ? (headed ? `<tr class="section sl-sub-head"><td colspan="8" class="sl-name">${words}</td></tr>` : '') + rows.map(row).join('') : '';
        return head + part('', missed, false)
            + part('Awaiting a statement', awaiting, true)
            + part('Paid as expected', paid, missed.length || awaiting.length);
    }).join('');
    return `${booksNav('bills')}
    <div class="page-head"><div><span class="eyebrow">Books</span><h1>Bills</h1><p>What renews and when, from the rows that pay it. A renewal with no payment seen also waits in the queue.</p></div>
        <div class="row"><button class="btn" data-act="bills-refresh">${icon('arrows-clockwise')}Refresh from rows</button><button class="btn" data-act="bill" data-id="">${icon('plus')}Add a bill</button></div></div>
    <section class="card">${sortPicker('bills', BILL_SORT, { own: 'Missed first' })}<div class="table-wrap"><table class="t sl-stack sl-bills"><thead><tr>${Object.keys(BILL_SORT).map(k =>
        `<th${BILL_SORT[k].kind === 'money' ? ' class="r"' : ''}>${sortHead('bills', k, BILL_SORT)}</th>`).join('')}</tr></thead>
        <tbody>${subs.length ? groups : '<tr><td colspan="8" class="empty sl-name">No bills yet.</td></tr>'}</tbody></table></div>
        <p class="card-foot">Monthly figures in S$ at each bill’s own rate. Each book’s bills are their own total; no line adds the books.</p></section>`;
}
// The cadences the sheet offers, in the words bills.py says them (COMMON_WORDS); a bill
// held at another keeps its own as one more choice ("every 2 months"), so saving never rewrites it.
const BILL_EVERY = [['week', 1, 'weekly'], ['week', 2, 'fortnightly'], ['month', 1, 'monthly'], ['month', 3, 'quarterly'], ['month', 6, 'half-yearly'], ['month', 12, 'yearly']];
function billEveryChoices(s) {
    const own = s?.cadence;
    const choices = BILL_EVERY.map(([unit, count, words]) => [`${unit}:${count}`, words]);
    if (own && !BILL_EVERY.some(([unit, count]) => unit === own.unit && count === own.count)) choices.push([`${own.unit}:${own.count}`, billEvery(s)]);
    // A cadence fin cannot read stays as it is held unless one is chosen.
    if (s && !own) choices.unshift(['', `unknown (${s.frequency})`]);
    return choices;
}
ACT.bill = async el => {
    const [r, services] = await Promise.all([refs(), servicesList()]);
    const s = el.dataset.id ? ACT.__subs.get(Number(el.dataset.id)) : null;
    const missed = s ? billMissed(s) : null;
    // A bill both awaiting its statement and in another currency gets one notice, the
    // awaiting line short above the currency fix, so the fields start high on a phone.
    const waits = s?.state === 'awaiting_statement';
    const awaitingNote = waits && !s.currency_mismatch ? `<div class="notice sl-missed">${icon('hourglass')}<div class="grow"><b>Awaiting ${esc(s.account_name || 'its account')}’s statement.</b>
        <div class="small">${esc(billWaitsOn(s, { named: false }))}. It counts as missed only if no payment shows once the statements cover the due day.</div></div></div>` : '';
    const currencyNote = s?.currency_mismatch ? `<div class="notice warn sl-missed">${icon('warning')}<div class="grow">${waits
        ? `<p class="small sl-also"><b>Awaiting a statement, not missed:</b> ${esc(billWaitsOn(s, { named: false }))}.</p>` : ''}<div id="bl-currency"><b>Billed in ${esc(s.currency)}, paid from ${esc(s.account_name || 'an account')} in ${esc(s.account_currency)}.</b>
        <div class="small">A bill is kept in its paying account’s currency.</div>
        <div class="row sl-missed-acts"><button type="button" class="btn sm" data-act="bill-currency" data-id="${s.id}">Change it to ${esc(s.account_currency)}…</button></div></div></div></div>` : '';
    const sel = (id, opts, v) => `<select id="${id}">${opts.map(([k, l]) => `<option value="${esc(k)}"${String(k) === String(v ?? '') ? ' selected' : ''}>${esc(l)}</option>`).join('')}</select>`;
    const paid = s && (s.tx_last_paid || s.last_paid);
    const missedNote = missed ? `<div class="notice warn sl-missed">${icon('hourglass')}<div class="grow"><b>No payment seen for ${esc(day(missed.due))}.</b>
        <div class="small">${paid ? `Last paid ${esc(money(toMinor(s.tx_amount ?? s.amount, s.currency || 'SGD'), s.currency || 'SGD'))} on ${esc(day(paid))}${s.account_name ? ` from ${esc(s.account_name)}` : ''}.` : 'No payment seen yet.'}${s.judged_by === 'today' ? ` fin holds no statements for ${s.account_name ? esc(s.account_name) : 'a paying account'}, so this is judged by today’s date alone.` : ''}</div>
        <div class="row sl-missed-acts"><button type="button" class="btn sm" data-act="bill-stopped" data-id="${s.id}">It has stopped</button>
            <button type="button" class="btn sm" data-act="bill-find" data-id="${s.id}">Find the payment</button>
            <button type="button" class="link" data-act="bill-queue" data-id="${s.id}">Open it in the queue</button></div></div></div>` : '';
    const own = s?.cadence ? `${s.cadence.unit}:${s.cadence.count}` : s ? '' : 'month:1';
    openSheet(s ? esc(s.service_name || 'Bill') : 'Add a bill', `${missedNote}${awaitingNote}${currencyNote}<div class="fields">
        <label class="field"><span>Merchant</span>${sel('bl-svc', [['', 'Choose'], ...services.map(x => [x.id, x.name])], s?.service_id)}</label>
        <div class="fields two"><label class="field"><span>Amount${s ? ` (${esc(s.currency || 'SGD')})` : ''}</span><input type="text" inputmode="decimal" id="bl-amount" value="${s ? esc(s.amount) : ''}"></label>
            <label class="field"><span>How often</span>${sel('bl-every', billEveryChoices(s), own)}</label></div>
        <label class="field"><span>Paid from</span>${sel('bl-acct', [['', '—'], ...r.accounts.filter(a => ['bank', 'card'].includes(a.type)).map(a => [a.id, a.name])], s?.account_id)}</label>
        ${s ? '' : '<p class="small muted">A bill is kept in the currency of the account that pays it.</p>'}
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
    queueOpen['out-bill'] = 9999;
    queueSearch = '';
    if (store.get('queueStep', '') !== 'bill') store.set('queueStep', '');
    store.set('queueFolded', store.get('queueFolded', []).filter(k => k !== 'out-bill'));
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
    const [unit, count] = v('bl-every').split(':');
    // The cadence, never a frequency: the server keeps how the bill holds it when it means the same,
    // and one fin cannot read is sent only when another is chosen.
    // No currency: a bill's is its paying account's, changed only by its previewed fix.
    const body = { service_id: Number(v('bl-svc')), amount: Number(v('bl-amount') || 0), ...(unit ? { every: { unit, count: Number(count) } } : {}),
        account_id: v('bl-acct') ? Number(v('bl-acct')) : null, renewal_date: v('bl-renew') || null,
        status: v('bl-status'), match_pattern: v('bl-pattern') || null, link: v('bl-link') || null, notes: v('bl-notes') || null };
    const r = el.dataset.id ? await act('PUT', `/api/subscriptions/${el.dataset.id}`, body, 'Bill saved') : await act('POST', '/api/subscriptions', body, 'Bill added');
    if (r) { closeSheet(); rerender(); }
};
// A bill in another currency than its paying account's: the change previewed in
// the sheet (before, after, where the new amount comes from), then made on its token.
ACT['bill-currency'] = async el => {
    const id = el.dataset.id;
    const r = await send('POST', `/api/bills/${id}/currency`, {});
    if (r.status !== 409 || !r.data.preview) { toast(r.data.error || 'That did not work; nothing was changed', { bad: true }); return; }
    const p = r.data.preview, to = p.after.currency;
    const basis = { last_payment: `the last payment seen, on ${day(p.basis.date)}`, rate: `the old amount at US$1 = S$${p.basis.rate}`, kept: 'the same figure' }[p.basis.kind] || '';
    const box = $('#bl-currency');
    if (!box) return;
    // Compact: the amount and the two answers on one line under the sentence.
    box.innerHTML = `<b>Change this bill to ${esc(to)}?</b>
        <div class="small">Now ${esc(money(toMinor(p.before.amount, p.before.currency), p.before.currency))}. After: ${esc(money(toMinor(p.after.amount, to), to))}, ${esc(basis)}. If the bill is a different amount in ${esc(to)}, change it here.</div>
        <div class="row sl-missed-acts sl-cur-fix"><label class="field"><span class="sr-only">Amount (${esc(to)})</span><input type="text" inputmode="decimal" id="bl-cur-amount" value="${esc(Number(p.after.amount).toFixed(2))}"></label>
            <button type="button" class="btn sm primary" data-act="bill-currency-apply" data-id="${esc(id)}" data-token="${esc(p.token)}">Change it</button>
            <button type="button" class="btn sm" data-act="bill" data-id="${esc(id)}">Keep it</button></div>`;
};
ACT['bill-currency-apply'] = async el => {
    const text = ($('#bl-cur-amount')?.value || '').trim();
    const amount = Number(text);
    if (!text || !Number.isFinite(amount) || amount <= 0) { toast('Give the amount as a figure above nothing; nothing was changed', { bad: true }); return; }
    const r = await act('POST', `/api/bills/${el.dataset.id}/currency`, { token: el.dataset.token, amount }, 'Bill changed to its account’s currency');
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
    const tabs = [['merchants', 'Merchants', 'coins'], ['rules', 'Rules', 'sliders-horizontal'], ['accounts', 'Accounts', 'bank'], ['rates', 'Rates', 'arrows-left-right'], ['types', 'Types', 'list-bullets'], ['books', 'Books', 'wallet']];
    const head = `${booksNav('lists')}<div class="page-head"><div><span class="eyebrow">Books</span><h1>Lists</h1><p>What the rules and labels are made of. Every change here lands in Changes, with Undo.</p></div></div>
        <nav class="seg sl-tabs" aria-label="Lists">${tabs.map(([k, l, ic]) => `<a href="#/books/lists/${k}"${k === tab ? ' aria-current="page"' : ''}>${icon(ic)}${l}</a>`).join('')}</nav>`;
    const search = placeholder => `<label class="field sl-search"><span class="sr-only">Search</span><input type="search" id="ls-search" value="${esc(listState.search)}" placeholder="${placeholder}"></label>`;
    const r = await refs();
    const needle = listState.search.toLowerCase();

    if (tab === 'types') {
        const [income, filings] = await Promise.all([get('/api/types?kind=income').catch(() => []), get('/api/types/slots').catch(() => ({ slots: [] }))]);
        // Every spending type is edited here: name, wording, settings (type-relationships 06). Other's meaning is
        // fixed: only its wording (fin RUL-004). Income kinds are set in code.
        const fix = t => t.kind === 'income' ? '<span class="small muted">Set in code</span>'
            : `${t.locked ? '<span class="small muted">name set in code</span> ' : ''}<button type="button" class="btn sm" data-act="type-edit" data-id="${t.id}" aria-label="Edit ${esc(t.display_name || t.name)}">Edit</button>`;
        const yes = v => (v ? 'yes' : '');
        const settings = t => t.locked ? 'not placed yet: fixed'
            : [t.usual_book, t.default_one_off ? 'one-off by default' : '', t.check_each_row ? 'Check each row by default' : ''].filter(Boolean).join(', ');
        const row = t => t.kind === 'income'
            ? `<tr><td class="sl-name"><b>${esc(t.display_name || t.name)}</b></td><td class="small">${esc(plainWords(t.covers))}</td>
            <td class="small muted">${esc(plainWords(t.not_for || ''))}</td>
            <td class="sl-sub">${esc(plainWords(t.covers))}${t.not_for ? `<div>Not for: ${esc(plainWords(t.not_for))}</div>` : ''}</td>
            <td class="r sl-end">${fix(t)}</td></tr>`
            : `<tr><td class="sl-name"><b>${esc(t.display_name || t.name)}</b>${t.added_in_fin ? ` ${tag('yours', 'added in fin')}` : ''}</td><td class="small">${esc(plainWords(t.covers))}</td>
            <td class="small muted">${esc(plainWords(t.not_for || ''))}</td>${t.locked ? '<td class="small muted" colspan="3">not placed yet: fixed</td>'
                : `<td class="small">${esc(t.usual_book)}</td><td class="small">${yes(t.default_one_off)}</td><td class="small">${yes(t.check_each_row)}</td>`}
            <td class="sl-sub">${esc(plainWords(t.covers))} · ${esc(settings(t))}${t.not_for ? `<div>Not for: ${esc(plainWords(t.not_for))}</div>` : ''}</td>
            <td class="r sl-end">${fix(t)}</td></tr>`;
        const cols = name => ({ name: { label: name, kind: 'text', of: t => t.display_name || t.name }, covers: { label: 'Covers', kind: 'text', of: t => plainWords(t.covers) },
            notfor: { label: 'Not for', kind: 'text', of: t => plainWords(t.not_for) } });
        const TYPE_SORT = { ...cols('Type'), book: { label: 'Usual book', kind: 'text', of: t => t.usual_book },
            oneoff: { label: 'One-off', kind: 'text', of: t => yes(t.default_one_off) }, each: { label: 'Check each row', kind: 'text', of: t => yes(t.check_each_row) } };
        const KIND_SORT = cols('Kind');
        const heads = (table, c) => Object.keys(c).map(k => `<th>${sortHead(table, k, c)}</th>`).join('');
        return `${head}<section class="card"><div class="section-header"><div><h2>Types</h2><p>One list serves every book. Add a type, or change a type’s name, wording or settings, here. Merging or removing a type is a code change.</p></div>
            <button class="btn" data-act="type-add">${icon('plus')}Add a type</button></div>
            ${sortPicker('types', TYPE_SORT)}<div class="table-wrap"><table class="t sl-stack"><thead><tr>${heads('types', TYPE_SORT)}<th></th></tr></thead><tbody>${sortRows('types', r.types.filter(t => t.kind === 'spending'), TYPE_SORT).map(row).join('')}</tbody></table></div>
            ${filingsHTML(filings.slots, r.types, income)}
            ${income.length ? `<h3 class="sl-subhead">Income kinds</h3>${sortPicker('income-kinds', KIND_SORT)}<div class="table-wrap"><table class="t sl-stack"><thead><tr>${heads('income-kinds', KIND_SORT)}<th></th></tr></thead><tbody>${sortRows('income-kinds', income, KIND_SORT).map(row).join('')}</tbody></table></div>`
                : '<p class="card-foot">Income kinds: none set up yet.</p>'}</section>`;
    }
    if (tab === 'books') {
        // Books are the operator's to add (fin RUL-021): every page, picker and chat tool reads this list.
        const BOOK_SORT = { name: { label: 'Book', kind: 'text', of: b => b.name }, holds: { label: 'What it holds', kind: 'text', of: b => b.description } };
        const row = (b, i) => `<tr><td class="sl-name"><b>${esc(b.name)}</b>${b.added_in_fin ? ` ${tag('yours', 'added in fin')}` : ''}</td><td class="small">${esc(b.description)}</td>
            <td class="small muted">${i === 0 ? 'the household' : 'a company: its costs move its balance, not household spending'}</td>
            <td class="sl-sub">${esc(b.description)}</td></tr>`;
        const order = new Map(r.books.map((b, i) => [b.name, i]));
        return `${head}<section class="card"><div class="section-header"><div><h2>Books</h2><p>Whose spending a row is. Each book has its own total and is never added to another. A book you add is listed everywhere books are: Home, Spending, the Queue’s book picker and chat. Renaming or removing a book is a code change.</p></div>
            <button class="btn" data-act="book-add">${icon('plus')}Add a book</button></div>
            ${sortPicker('books', BOOK_SORT, { own: 'Household first' })}<div class="table-wrap"><table class="t sl-stack"><thead><tr><th>${sortHead('books', 'name', BOOK_SORT)}</th><th>${sortHead('books', 'holds', BOOK_SORT)}</th><th>Counts as</th></tr></thead><tbody>${sortRows('books', r.books, BOOK_SORT).map(b => row(b, order.get(b.name))).join('')}</tbody></table></div></section>`;
    }
    if (tab === 'rates') {
        const saved = await get('/api/rates');
        const foreign = [...new Set(r.accounts.filter(a => a.currency && a.currency !== 'SGD').map(a => a.currency))];
        const RATE_SORT = { day: { label: 'Day', kind: 'date', of: x => x.date }, pair: { label: 'Pair', kind: 'text', of: x => x.pair },
            rate: { label: 'Rate', kind: 'number', of: x => x.rate }, source: { label: 'Where from', kind: 'text', of: x => plainWords(x.source) } };
        // The newest 200 are listed, and those sort.
        const rows = sortRows('rates', saved.slice(0, 200), RATE_SORT).map(x => `<tr><td class="num sl-name">${esc(day(x.date))}</td><td>${esc(x.pair)}</td><td class="r num sl-fig">${esc(x.rate)}</td><td class="small muted">${esc(plainWords(x.source))}</td>
            <td class="sl-sub">${esc(x.pair)}${x.source ? ' · ' + esc(plainWords(x.source)) : ''}</td>
            <td class="r sl-end"><button class="btn sm" data-act="rate" data-currency="${esc(String(x.pair).split('/')[0])}" data-date="${esc(x.date)}">Change</button></td></tr>`).join('');
        return `${head}<section class="card"><div class="section-header"><div><h2>Rates</h2><p>S$ for one unit, by day. A balance in another currency joins the S$ totals at the rate for its day, or the latest saved before it.</p></div>
            <div class="row">${(foreign.length ? foreign : ['INR']).map(c => `<button class="btn" data-act="rate" data-currency="${esc(c)}" data-date="${esc(rateDay())}">Fetch or enter a ${esc(c)} rate</button>`).join('')}</div></div>
            ${sortPicker('rates', RATE_SORT, { own: 'Newest first' })}<div class="table-wrap"><table class="t sl-stack"><thead><tr><th>${sortHead('rates', 'day', RATE_SORT)}</th><th>${sortHead('rates', 'pair', RATE_SORT)}</th><th class="r">${sortHead('rates', 'rate', RATE_SORT)}</th><th>${sortHead('rates', 'source', RATE_SORT)}</th><th></th></tr></thead><tbody>${rows || '<tr><td colspan="5" class="empty sl-name">No rate saved yet.</td></tr>'}</tbody></table></div>
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
                if (a.type === 'untracked') return 'untracked: no statement or figure';
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
        // Latest balance held sorts by its S$ figure: the sheet's, or the figure itself in an S$ account.
        const ACCOUNT_SORT = { name: { label: 'Account', kind: 'text', of: a => a.name }, kind: { label: 'Kind', kind: 'text', of: a => a.type },
            whose: { label: 'Whose', kind: 'text', of: a => a.owner }, currency: { label: 'Currency', kind: 'text', of: a => a.currency },
            rests: { label: 'What its balance rests on', kind: 'date', of: a => a.anchor?.date },
            balance: { label: 'Latest balance held', kind: 'money', of: a => lineOf.get(a.id)?.value_minor ?? (a.currency === 'SGD' ? a.anchor?.amount_minor : null) } };
        const rows = sortRows('accounts', r.accounts, ACCOUNT_SORT).map(a => `<tr><td class="sl-name"><a href="#/books/account/${a.id}"><b>${esc(a.name)}</b></a>${accountMark(a.id)}</td><td>${esc(a.type)}</td><td>${esc(a.owner)}</td><td>${esc(a.currency)}</td>
            <td class="small sl-rests">${restsCell(a)}</td>
            <td class="r num sl-fig">${a.anchor ? `<span class="${a.anchor.amount_minor < 0 ? 'neg' : ''}">${esc(money(a.anchor.amount_minor, a.currency))}</span>` : ''}</td>
            <td class="sl-sub">${esc(a.type)} · ${restsCell(a)}</td>
            <td class="r sl-end"><button class="btn sm" data-act="account-edit" data-id="${a.id}">Edit</button></td></tr>`).join('');
        return `${head}<section class="card"><div class="section-header"><h2>Accounts</h2><button class="btn" data-act="account-edit" data-id="">${icon('plus')}Add an account</button></div>
            ${sortPicker('accounts', ACCOUNT_SORT)}<div class="table-wrap"><table class="t sl-stack sl-accounts"><thead><tr>${['name', 'kind', 'whose', 'currency', 'rests'].map(k => `<th>${sortHead('accounts', k, ACCOUNT_SORT)}</th>`).join('')}<th class="r">${sortHead('accounts', 'balance', ACCOUNT_SORT)}</th><th></th></tr></thead><tbody>${rows}</tbody></table></div>
            <p class="card-foot">Stale and missing figures say so, and open their fix. Owed balances are negative.</p></section>`;
    }
    if (tab === 'rules') {
        // The drift count (fin-surfaces 51) is a read beside the list: one it cannot give leaves the line undrawn.
        const [rules, drift] = await Promise.all([get('/api/rules'), get('/api/rules/drift').catch(() => ({ count: 0 }))]);
        const shown = rules.filter(x => !needle || `${x.pattern} ${x.service_name}`.toLowerCase().includes(needle));
        ACT.__rules = new Map(rules.map(x => [x.id, x]));
        const sets = x => ruleSets(x, r);
        const RULE_SORT = { pattern: { label: 'Pattern', kind: 'text', of: x => x.pattern }, match: { label: 'Match', kind: 'text', of: x => MATCH_WORDS[x.match_type] || x.match_type },
            merchant: { label: 'Merchant', kind: 'text', of: x => x.service_name }, sets: { label: 'Sets', kind: 'text', of: x => plainText(sets(x)) },
            rows: { label: 'Rows it labelled', kind: 'number', of: x => x.rows_labelled } };
        const rows = sortRows('rules', shown, RULE_SORT).slice(0, 200).map(x => `<tr class="clickable" data-act="rule" data-id="${x.id}"><td class="sl-name"><b class="mono-ish">${esc(x.pattern)}</b></td><td class="small">${esc(MATCH_WORDS[x.match_type] || x.match_type)}</td>
            <td>${esc(x.service_name || '')}</td><td class="small">${sets(x)}</td>
            <td class="r num sl-fig">${x.rows_labelled ?? ''}<span class="sl-phone-word"> ${x.rows_labelled === 1 ? 'row' : 'rows'}</span></td>
            <td class="sl-sub">${esc(MATCH_WORDS[x.match_type] || x.match_type)} · gives ${esc(x.service_name || '')}${sets(x) ? ' · ' + sets(x) : ''}</td></tr>`).join('');
        return `${head}<section class="card"><div class="section-header">${search('Search patterns or merchants')}
            <div class="row">${driftLine(drift)}<button class="btn" data-act="rule" data-id="">${icon('plus')}Add a rule</button></div></div>
            <p class="card-foot sl-lead">${shown.length} of ${rules.length} rules${shown.length > 200 ? ' · the first 200 shown' : ''}. A rule gives a row its merchant, and with it a book and type.</p>
            ${sortPicker('rules', RULE_SORT)}<div class="table-wrap"><table class="t sl-stack"><thead><tr>${['pattern', 'match', 'merchant', 'sets'].map(k => `<th>${sortHead('rules', k, RULE_SORT)}</th>`).join('')}<th class="r">${sortHead('rules', 'rows', RULE_SORT)}</th></tr></thead><tbody>${rows || '<tr><td colspan="5" class="empty sl-name">No rule matches.</td></tr>'}</tbody></table></div></section>`;
    }
    // merchants
    const services = await servicesList();
    const filters = { all: () => true, untyped: s => !s.type_id, each: s => s.review_each_time, hidden: s => s.exclude_from_expense_views, unused: s => !s.txn_count };
    const shown = services.filter(filters[listState.filter] || filters.all).filter(s => !needle || s.name.toLowerCase().includes(needle));
    const renaming = listState.renaming;
    const rows = sortRows('merchants', shown, MERCHANT_SORT).slice(0, 200).map(s => merchantListRow(s, { renaming, renames: listState.renames })).join('');
    const chip = (k, l, n) => `<button class="chip" data-act="list-filter" data-f="${k}" aria-pressed="${listState.filter === k}">${l} <span class="n">${n}</span></button>`;
    const pending = Object.keys(listState.renames).length;
    const ask = !renaming && services.some(filters.untyped) && (await suggestionsOn()) ? suggestEntryHTML({ lists: true }) : '';
    return `${head}<section class="card"><div class="section-header">${search('Search merchants')}
            <div class="row">${renaming
                ? `<button class="btn" data-act="rename-mode" data-on="0">Stop renaming</button><button class="btn primary" data-act="rename-save" id="rename-save"${pending ? '' : ' disabled'}>${pending ? `Save ${plural(pending, 'rename')}` : 'Save renames'}</button>`
                : `<button class="btn" data-act="rename-mode" data-on="1">${icon('pencil-simple')}Rename several</button><button class="btn" data-act="merchant-add">${icon('plus')}Add a merchant</button>`}</div></div>
        ${renaming ? '<p class="card-foot sl-lead">Type the new names, then save them together: one change in Changes, with Undo. Rules and rows keep pointing at the same merchant.</p>' : ''}
        ${ask}<div class="chips sl-chips">${chip('all', 'All', services.length)}${chip('untyped', 'No type', services.filter(filters.untyped).length)}${chip('each', 'Check each row', services.filter(filters.each).length)}${chip('hidden', 'Hidden', services.filter(filters.hidden).length)}${chip('unused', 'No rows', services.filter(filters.unused).length)}</div>
        ${sortPicker('merchants', MERCHANT_SORT)}<div class="table-wrap"><table class="t sl-stack${renaming ? ' sl-renaming' : ''}"><thead><tr>${['name', 'book', 'type'].map(k => `<th>${sortHead('merchants', k, MERCHANT_SORT)}</th>`).join('')}<th class="r">${sortHead('merchants', 'rows', MERCHANT_SORT)}</th><th class="r">${sortHead('merchants', 'rules', MERCHANT_SORT)}</th><th></th>${renaming ? '' : '<th><span class="sr-only">Edit</span></th>'}</tr></thead><tbody>${rows || `<tr><td colspan="${renaming ? 6 : 7}" class="empty sl-name">None.</td></tr>`}</tbody></table></div>
        ${shown.length > 200 ? `<p class="card-foot">The first 200 of ${shown.length}; search to narrow.</p>` : ''}</section>`;
}
/** Lists › Merchants' sortable columns; the marks and the pencil do not sort. */
const MERCHANT_SORT = { name: { label: 'Merchant', kind: 'text', of: s => s.name }, book: { label: 'Book', kind: 'text', of: s => s.book },
    type: { label: 'Type', kind: 'text', of: s => s.type_id ? s.display_type || s.type_name : null },
    rows: { label: 'Rows', kind: 'number', of: s => s.txn_count }, rules: { label: 'Rules', kind: 'number', of: s => s.rule_count } };
/** One row of Lists › Merchants. The row opens the merchant's page; the pencil at its end
 *  opens Edit the merchant in place (its own data-act, so the row's never sees the click).
 *  While renaming several, the name is an input, and the row and the pencil are gone. */
function merchantListRow(s, { renaming, renames }) {
    const nameCell = renaming
        ? `<td class="sl-name sl-wide"><input type="text" class="rename-input" data-rename="${s.id}" data-orig="${esc(s.name)}" value="${esc(renames[s.id] ?? s.name)}" aria-label="New name for ${esc(s.name)}"></td>`
        : `<td class="sl-name"><b>${esc(s.name)}</b></td>`;
    const typeCell = s.type_id ? esc(s.display_type || s.type_name) : tag('nofig', 'no type', { act: 'merchant', data: { id: s.id }, title: 'Give it a type' });
    const marks = `${s.review_each_time ? tag('', 'Check each row') : ''} ${s.exclude_from_expense_views ? tag('yours', 'hidden') : ''} ${s.is_one_off ? tag('yours', 'one-off') : ''}`.trim();
    return `<tr${renaming ? '' : ` class="clickable" data-act="merchant-page" data-id="${s.id}"`}>${nameCell}<td>${esc(s.book || '')}</td>
        <td>${typeCell}</td><td class="r num${renaming ? '' : ' sl-fig'}">${s.txn_count}<span class="sl-phone-word"> ${s.txn_count === 1 ? 'row' : 'rows'}</span></td><td class="r num">${s.rule_count}</td>
        <td>${marks}</td>
        ${renaming ? '' : `<td class="sl-sub">${esc(s.book || 'no book')} · ${typeCell} · ${plural(s.rule_count, 'rule')} ${marks}</td>
        <td class="r sl-end"><button type="button" class="btn sm subtle icon" data-act="merchant" data-id="${s.id}" aria-label="Edit ${esc(s.name)}" title="Edit ${esc(s.name)}">${icon('pencil-simple')}</button></td>`}</tr>`;
}
// Lists › Types (fin RUL-002): add a spending type, or edit one: its name, its
// wording (what it covers, what it is not for) and its settings (type-relationships
// 06). Jev is sent the same words. The server refuses
// what is not allowed and says why; the sheet stays open on a refusal.
const TYPE_WORDS_MOST = 300;
function typeWordingFields(t) {
    return `<label class="field"><span>Covers</span><textarea id="ty-covers" rows="3" maxlength="${TYPE_WORDS_MOST}" placeholder="What goes under it, as a list: nannies, after-school care">${esc(t?.covers || '')}</textarea>
            <span class="hint">Jev reads this to choose a type for a new merchant.</span></label>
        <label class="field"><span>Not for</span><textarea id="ty-notfor" rows="2" maxlength="${TYPE_WORDS_MOST}" placeholder="What looks like it but belongs elsewhere">${esc(t?.not_for || '')}</textarea></label>`;
}
ACT['type-add'] = async () => {
    const r = await refs();
    // A type goes at the top of the list or under a top-level type, never deeper.
    const tops = r.types.filter(t => t.kind === 'spending' && !t.parent_id);
    openSheet('Add a type', `<div class="fields">
        <label class="field"><span>Name</span><input type="text" id="ty-name" maxlength="40"></label>
        <label class="field"><span>Under</span><select id="ty-parent"><option value="">Nothing: a type of its own</option>${tops.map(t => `<option value="${t.id}">${esc(t.name)}</option>`).join('')}</select></label>
        <label class="check"><input type="checkbox" id="ty-oneoff"> One-off by default: a row of this type is one-off unless you say otherwise</label>
        ${typeWordingFields(null)}</div>
        <p class="hint">Merging or removing a type is a code change.</p>`,
    { eyebrow: 'Types', foot: '<button class="btn primary" data-act="type-add-save">Add the type</button>' });
};
/** Send a type write; on success toast the type's name with Undo and redraw, on a refusal say the server's words. */
async function typeWrite(el, method, url, body, done) {
    el.disabled = true;
    const r = await send(method, url, body);
    el.disabled = false;
    if (!r.ok) { toast(r.data.error || 'That did not work; nothing was changed', { bad: true }); return; }
    toast(done(r.data.type.display_name), { undo: r.change });
    refreshFrame();
    closeSheet();
    rerender();
}
ACT['book-add'] = () => {
    openSheet('Add a book', `<div class="fields">
        <label class="field"><span>Name</span><input type="text" id="bk-name" maxlength="30"></label>
        <label class="field"><span>What it holds</span><input type="text" id="bk-desc" placeholder="costs of the film, whichever account paid them"></label></div>
        <p class="hint">A book other than Household is a company: its costs move its company balance, not household spending. Renaming or removing a book is a code change.</p>`,
    { eyebrow: 'Books', foot: '<button class="btn primary" data-act="book-add-save">Add the book</button>' });
};
ACT['book-add-save'] = async el => {
    el.disabled = true;
    const r = await send('POST', '/api/books', { name: $('#bk-name').value, description: $('#bk-desc').value });
    el.disabled = false;
    if (!r.ok) { toast(r.data.error || 'That did not work; nothing was changed', { bad: true }); return; }
    toast(`Added the book ${r.data.book.name}`, { undo: r.change });
    refreshFrame();
    closeSheet();
    rerender();
};
ACT['type-add-save'] = el => {
    const parent = $('#ty-parent').value;
    return typeWrite(el, 'POST', '/api/types', { name: $('#ty-name').value, parent_id: parent ? Number(parent) : null,
        one_off: $('#ty-oneoff').checked, covers: $('#ty-covers').value, not_for: $('#ty-notfor').value }, shown => `Added the type ${shown}`);
};
// A type's Edit sheet (type-relationships 06): its name, its wording, its usual
// book, one-off and Check each row by default, saved as one change.
ACT['type-edit'] = async el => {
    const r = await refs();
    const t = r.typeById.get(Number(el.dataset.id));
    if (!t) { toast('That type is no longer in fin', { bad: true }); return; }
    // Other means not placed yet: its name and settings are fixed, its wording is not (fin RUL-004).
    if (t.locked) {
        openSheet(esc(t.display_name || t.name), `<div class="fields">${typeWordingFields(t)}</div>
            <p class="hint">${esc(t.name)} means not placed yet: its name and settings are fixed.</p>`,
        { eyebrow: 'Edit the type', foot: `<button class="btn primary" data-act="type-edit-save" data-id="${t.id}">Save</button>` });
        return;
    }
    const books = [...r.books.map(b => b.name), 'ask'];
    const parent = t.parent_id ? r.typeById.get(t.parent_id) : null;
    openSheet(esc(t.display_name || t.name), `<div class="fields">
        <label class="field"><span>Name</span><input type="text" id="ty-name" maxlength="40" value="${esc(t.name)}">
            <span class="hint">${parent ? `Under ${esc(parent.name)}. ` : ''}Merchants, rules and rows keep pointing at it; Jev’s answers follow the new name.</span></label>
        ${typeWordingFields(t)}
        <label class="field"><span>Usual book</span><select id="ty-book">${books.map(b => `<option value="${esc(b)}"${b === t.usual_book ? ' selected' : ''}>${b === 'ask' ? 'ask: you say whose it is' : esc(b)}</option>`).join('')}</select>
            <span class="hint">The book a new merchant of this type is proposed. Merchants you have already placed keep theirs.</span></label>
        <label class="check"><input type="checkbox" id="ty-oneoff"${t.default_one_off ? ' checked' : ''}> One-off by default: a row of this type is one-off unless you say otherwise</label>
        <label class="check"><input type="checkbox" id="ty-each"${t.check_each_row ? ' checked' : ''}> Check each row by default: a new merchant of this type starts as Check each row</label></div>
        <p class="hint">Merging or removing a type is a code change.</p>`,
    { eyebrow: 'Edit the type', foot: `<button class="btn primary" data-act="type-edit-save" data-id="${t.id}">Save</button>` });
};
/** What an Edit sheet's save did, in the toast's words: as Changes names it. */
function typeEditWords(sent, was, shown) {
    const settings = [
        'usual_book' in sent ? (sent.usual_book === 'ask' ? 'usual book: ask' : `usual book ${sent.usual_book}`) : '',
        'one_off' in sent ? (sent.one_off ? 'one-off by default' : 'running by default') : '',
        'check_each_row' in sent ? (sent.check_each_row ? 'Check each row by default' : 'Every row the same by default') : '',
    ].filter(Boolean).join(', ');
    const wording = 'covers' in sent || 'not_for' in sent;
    if ('name' in sent) return `Renamed ${was} to ${shown}${settings ? `, ${settings}` : ''}${wording ? ', wording fixed' : ''}`;
    if (settings) return `Changed ${shown}: ${settings}${wording ? ', wording fixed' : ''}`;
    return `Fixed the wording of ${shown}`;
}
// Only what the operator changed is sent: one change entry, named for it.
ACT['type-edit-save'] = async el => {
    const t = (await refs()).typeById.get(Number(el.dataset.id));
    if (!t) { toast('That type is no longer in fin', { bad: true }); return; }
    const sent = {};
    const typed = (id, field, was) => { const v = $(id).value; if (v !== was) sent[field] = v; };
    typed('#ty-covers', 'covers', t.covers || '');
    typed('#ty-notfor', 'not_for', t.not_for || '');
    if (!t.locked) {
        typed('#ty-name', 'name', t.name);
        typed('#ty-book', 'usual_book', t.usual_book);
        if ($('#ty-oneoff').checked !== Boolean(t.default_one_off)) sent.one_off = $('#ty-oneoff').checked;
        if ($('#ty-each').checked !== Boolean(t.check_each_row)) sent.check_each_row = $('#ty-each').checked;
    }
    if (!Object.keys(sent).length) { toast(`Nothing to save: ${t.display_name || t.name} is as it was`); closeSheet(); return; }
    return typeWrite(el, 'PUT', `/api/types/${t.id}`, sent, shown => typeEditWords(sent, t.display_name || t.name, shown));
};

// fin's own filings (type-relationships 03, 06): the type each thing fin files
// itself goes under, a picker per slot. A move goes through the preview: where
// fin can tell the past rows it filed (loan interest), apply to them or new
// rows only; where it cannot (a gift), new rows only, and it says so.
const SLOT_WORDS = { loan_interest: 'Loan interest worked out from your figures', gift_given: 'A gift given', gift_received: 'A gift received' };
function filingsHTML(slots, types, income) {
    if (!slots.length) return '';
    // Other means not placed yet: fin files nothing under it, of either kind. Its name is fixed.
    const notPlaced = types.find(t => t.kind === 'spending' && t.locked)?.name;
    const offered = s => (s.kind === 'income' ? income : types.filter(t => t.kind === 'spending')).filter(t => t.name !== notPlaced || t.parent_id);
    const picker = s => `<select class="sel" data-change="slot-move" data-slot="${esc(s.slot)}" aria-label="The type fin files ${esc(s.files)} under">${offered(s).map(t =>
        `<option value="${t.id}"${t.id === s.type_id ? ' selected' : ''}>${esc(t.display_name || t.name)}</option>`).join('')}</select>`;
    return `<h3 class="sl-subhead">fin’s own filings</h3>
        <p class="card-foot sl-lead">What fin files itself goes under these types. Moving one asks about the past rows first.</p>
        <div class="slots">${slots.map(s => `<div class="slot-row"><span class="slot-what">${esc(SLOT_WORDS[s.slot] || s.files)}${s.kind === 'income' ? ' <span class="small muted">(income kind)</span>' : ''}</span>
            ${picker(s)}</div>`).join('')}</div>`;
}
/** The drawer's parts for a slot move's preview. The type names come from the picker: an income kind too. */
function slotParts(p, from, to) {
    let asked = p.past_known
        ? `<p class="q">${plural(p.count, 'past row')} fin filed as ${esc(p.subject.name)}</p>
            <p class="sub">Move them to ${esc(to)} too, or only new rows go there.</p>
            <div class="tshell ex-shell">${bandedTable({ caption: 'Examples, newest first', cls: 'ex', columns: [{ label: 'Date' }, { label: 'Amount', cls: 'r' }, { label: 'Now → after' }],
                rows: p.examples.map(x => `<tr><td class="num">${esc(day(x.date))}</td><td class="r num">${esc(money(x.amount_minor, x.currency))}</td><td>${esc(from)} → <b>${esc(to)}</b></td></tr>`) })}</div>`
        : `<p class="sub">fin cannot tell which past rows it filed as ${esc(p.subject.name)}, so only new rows go under ${esc(to)}. Past rows keep their type.</p>`;
    asked += flowsLine(p);
    const foot = `<button type="button" class="btn ghost" data-act="close-sheet">Cancel</button><span class="sp"></span>${p.past_known
        ? `<button type="button" class="btn" data-act="preview-answer" data-past="only_new">Only new rows</button><button type="button" class="btn primary" data-act="preview-answer" data-past="apply">Apply to ${plural(p.count, 'past row')}</button>`
        : '<button type="button" class="btn primary" data-act="preview-answer" data-past="only_new">Only new rows</button>'}`;
    return { eyebrow: 'fin’s own filings', title: `File ${esc(p.subject.name)} under ${esc(to)}?`,
        body: `<ul class="diff"><li>Type: <s>${esc(from)}</s> → <b>${esc(to)}</b></li></ul>${asked}`, foot };
}
ACT['slot-move'] = async el => {
    const slot = el.dataset.slot;
    const typeId = Number(el.value);
    const [{ slots }, income, r] = await Promise.all([get('/api/types/slots', { fresh: true }), get('/api/types?kind=income'), refs()]);
    const s = slots.find(x => x.slot === slot);
    if (!s || s.type_id === typeId) return;
    const to = [...r.types, ...income].find(t => t.id === typeId);
    const shown = to ? (to.display_name || to.name) : 'that type';
    const res = await sendPreviewed('PUT', `/api/types/slots/${slot}`, { type_id: typeId }, { parts: p => slotParts(p, s.display_type, shown) });
    if (!res.cancelled && !res.ok) toast(res.data.error || 'That did not work; nothing was changed', { bad: true });
    if (res.ok) {
        const n = res.data.recategorized;
        toast(`fin files ${s.files} under ${shown}${n ? `, with its ${plural(n, 'past row')}` : res.answered?.past === 'only_new' ? ', new rows only' : ''}. One change in Changes, with Undo.`, { undo: res.change });
        refreshFrame();
    }
    // Drawn again either way: a closed preview or a refusal puts the picker back as it was.
    rerender();
};

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

// The empty book choice reads the same in both sheets; the line under it says
// what it does in each. Adding, the route gives the type's usual book
// (_book_or_proposed); editing, no book is saved, and a row with no book is
// read as Household (scope.book_expr) unless its rule or the operator gave it one.
function merchantFields(r, s) {
    const bookLine = s ? 'With no book chosen, its rows are read as Household, unless a rule or you give one another book.'
        : 'With a type chosen, fin gives it the type’s usual book when you add it.';
    return `<label class="field"><span>Name</span><input type="text" id="mc-name" value="${esc(s?.name || '')}"></label>
        <div class="fields two"><label class="field"><span>Book</span><select id="mc-book">${bookOptions(r.books, s?.book, 'No book chosen')}</select><span class="hint">${esc(bookLine)}</span></label>
            <label class="field"><span>Type</span><select id="mc-type"${s ? '' : ' data-change="mc-type-pick"'}>${typeOptions(r.types, s?.type_id, { blank: 'No type' })}</select></label></div>
        <label class="field"><span>Note</span><textarea id="mc-notes" rows="2" placeholder="Anything worth remembering about it">${esc(s?.notes || '')}</textarea></label>
        ${s ? eachSettingHTML(s) : '<label class="check"><input type="checkbox" id="mc-each" data-change="mc-each-touch"> Check each row: each new row waits in the Queue for you to check it</label>'}
        <label class="check"><input type="checkbox" id="mc-hidden"${s?.exclude_from_expense_views ? ' checked' : ''}> Hide from spending lists (never from a sum)</label>`;
}
// The Check each row setting (fin-surfaces 12): one route turns it on or
// off, apart from the merchant's Save. Turning it on offers once to bring the
// merchant's past rows to the Queue, off unless chosen; turning it off writes
// at once, and its rows keep their labels. The switch lives on the merchant
// page; the edit sheet (Lists, the page's Edit) shows the setting and links
// there, as a switch that writes at once would close the sheet on its edits.
function eachSettingHTML(s) {
    const on = !!s.review_each_time;
    return `<div class="field each-set"><span>How its new rows arrive</span>
        <p class="small"><b>${on ? 'Check each row' : 'Every row the same'}</b>${on && s.rows_to_check ? ` · ${plural(s.rows_to_check, 'row')} not checked yet` : ''}</p>
        <span class="hint">Change it on <a class="link" href="#/books/merchant/${s.id}" data-act="each-page" data-id="${s.id}">the merchant page</a>, where it saves at once.</span></div>`;
}
// The link closes the sheet even when the page is already open behind it.
ACT['each-page'] = el => {
    closeSheet();
    const to = `#/books/merchant/${el.dataset.id}`;
    if (location.hash !== to) location.hash = to;
};
function eachSegHTML(s, labelledBy) {
    const on = !!s.review_each_time;
    const type = s.display_type || s.type_name || 'no type';
    return `<div class="seg" role="group" aria-labelledby="${labelledBy}">
            <button type="button" data-act="each-set" data-id="${s.id}" data-on="0" aria-pressed="${!on}">Every row the same</button>
            <button type="button" data-act="each-set" data-id="${s.id}" data-on="1" aria-pressed="${on}">Check each row</button></div>
        <span class="sub">${on ? `Each new row arrives as ${esc(type)} in its usual book, marked not checked, and waits in the Queue.`
            : `Every new row is ${esc(type)} · ${esc(s.book || 'no book')}, without asking.`}</span>`;
}
async function merchantById(id) { return (await servicesList()).find(s => s.id === id) || null; }
ACT['each-set'] = async el => {
    const s = await merchantById(Number(el.dataset.id));
    if (!s) { toast('This merchant is no longer in fin', { bad: true }); return; }
    const on = el.dataset.on === '1';
    if (!!s.review_each_time === on) return;
    if (!on) {
        const r = await act('POST', `/api/services/${s.id}/check-each-row`, { on: false },
            `${s.name}: every new row will be labelled ${s.display_type || s.type_name || 'no type'} · ${s.book || 'no book'}.`);
        if (r) { closeSheet(); rerender(); }
        return;
    }
    if (!s.rows_to_check) { await checkEachRowOn(s, 'leave'); return; }
    openEachOffer(s, 'each-on');
};
/** The offer, once, as a merchant fin holds turns to Check each row: its
 *  past rows stay as they are (checked) or come to the Queue. act answers. */
function openEachOffer(s, act) {
    const n = plural(s.rows_to_check, 'past row');
    openSheet(esc(s.name), `<p class="q">Also bring its ${n} to the Queue to check?</p>
        <ul class="diff"><li>New rows: <s>Every row the same</s> → <b>Check each row</b></li></ul>
        <p class="say">Either way, each new row from ${esc(s.name)} will wait in the Queue, marked not checked. Left where they are, the ${n} ${s.rows_to_check === 1 ? 'counts' : 'count'} as checked.</p>`,
    { eyebrow: 'Check each row', center: true,
      foot: `<button type="button" class="btn ghost" data-act="close-sheet">Cancel</button><span class="sp"></span><button type="button" class="btn" data-act="${act}" data-id="${s.id}" data-past="queue">Bring them too</button><button type="button" class="btn primary" data-act="${act}" data-id="${s.id}" data-past="leave">Not now, only new rows</button>` });
}
// Teaching a merchant fin holds as Check each row (fin-surfaces 13) asks the
// offer before its save: the answer, or null when the sheet is closed
// (pastAnswer, kept beside the sheet so closeSheet can answer it).
function askPast(s) {
    return new Promise(done => { openEachOffer(s, 'past-answer'); pastAnswer = done; });
}
ACT['past-answer'] = el => {
    const answer = pastAnswer;
    pastAnswer = null;
    closeSheet();
    if (answer) answer(el.dataset.past);
};

// The preview (fin-surfaces 21): a change that would relabel past rows is
// answered first by the server with what it would do (409, `preview`), and
// asked once in the one-confirm drawer: Cancel, Only new rows, or Apply to N
// past rows, neither answer focused first. The answer goes back with the
// preview's token; a token that no longer holds is refused by the server.
const PREVIEW_FIELD = { book: ['book', 'Book'], type_id: ['type', 'Type'], wording: ['wording', 'Recognises'] };
// A rule's preview (fin-surfaces 50) is titled by what is done to the rule.
const PREVIEW_TITLE = { add: 'Add the rule', edit: 'Save the rule', delete: 'Delete the rule' };
/** The drawer's parts for a preview: {eyebrow, title, body, foot}. A
 *  merchant's book or type, a merge (the merchant line, then each label that
 *  changes), a rule (its changed fields), or a wording taught from the Queue
 *  (fin-surfaces 53). The rows another merchant holds, a rule's or a taught
 *  wording's, are named in one warning line, as mocked (21, screen 2). See
 *  all only for a change to a merchant's own book or type: a rule's rows sit
 *  with several merchants, a taught wording's with another merchant or none
 *  yet, and a merchant not made yet has no page. `taught`: a teach's own row,
 *  marked "this row" among the examples. */
function previewParts(p, { taught = null } = {}) {
    const name = p.subject.name;
    const kind = p.subject.kind;
    if (kind === 'drift') return driftParts(p);
    const fieldLines = p.change.filter(c => c.label || c.from !== c.to);
    const words = fieldLines.map(c => (c.label ? [c.label.toLowerCase(), c.label] : (PREVIEW_FIELD[c.field] || [c.field, c.field])));
    const only = fieldLines.length === 1 ? fieldLines[0] : null;
    // A teach whose only question is its rows' flows changes no field of
    // the merchant: it is asked as applying the merchant to those rows.
    const title = kind === 'rule' ? `${PREVIEW_TITLE[p.subject.verb] || 'Save the rule'} ${esc(name)}?`
        : kind === 'merge' ? `Merge ${esc(name)} into ${esc(p.subject.into.name)}?`
        : !fieldLines.length ? `Apply ${esc(name)} to these rows?`
        : only && only.field === 'wording' ? `Teach ${esc(name)} the wording ${esc(only.to)}?`
        : only ? `Change ${esc(name)}’s ${words[0][0]} to ${esc(only.to)}?` : `Change ${esc(name)}’s ${words.map(w => w[0]).join(' and ')}?`;
    const eyebrow = kind === 'rule' ? 'Rule' : kind === 'merge' ? 'Merge'
        : fieldLines.length ? `${esc(name)} · ${words.map(w => w[0]).join(' and ')}` : esc(name);
    const line = (label, from, to) => `<li>${label}: ${[from != null ? `<s>${esc(from)}</s>` : '', to != null ? `<b>${esc(to)}</b>` : ''].filter(Boolean).join(' → ')}</li>`;
    const lines = [kind === 'merge' ? line('Merchant', name, p.subject.into.name) : '', ...fieldLines.map((c, i) => line(words[i][1], c.from, c.to))].join('');
    // Where the rows would move: a rule's merchant, or the merchant taught.
    const movesTo = esc(kind === 'rule' ? (p.subject.merchant ? p.subject.merchant.name : '') : name);
    const warns = (p.others || []).length ? `<div class="warnline">${icon('info')}<span><b>${p.others.map(o => `${plural(o.count, 'row')} now ${esc(o.name)}’s`).join(', ')}</b> would move to ${movesTo}.</span></div>` : '';
    const ruleLines = kind !== 'rule' ? '' : [
        p.unheld ? `<p class="sub">${plural(p.unheld, 'row')} with no merchant yet would become ${movesTo}.</p>` : '',
        p.subject.verb === 'delete' && p.labelled ? `<p class="sub">${plural(p.labelled, 'row')} ${p.labelled === 1 ? 'is' : 'are'} labelled by this rule.</p>` : '',
    ].join('');
    const span = m => m.first === m.last ? monthName(m.first, { short: true }) : `${monthName(m.first, { short: true })} – ${monthName(m.last, { short: true })}`;
    const facts = p.money && p.months ? `<div class="prev-facts">
        <div><span class="cap">Moves</span>${p.money.map(m => `<b class="num">${esc(money(m.amount_minor))}</b><span class="sub">${esc(m.from)} → ${esc(m.to)}</span>`).join('')}</div>
        <div><span class="cap">Months</span><b class="num">${p.months.count}</b><span class="sub">${esc(span(p.months))}</span></div></div>` : '';
    const leftOut = (p.left_out || []).length ? `<p class="sub">Not in the S$ figure: ${p.left_out.map(x => `${esc(money(x.amount_minor, x.currency))} in ${esc(monthName(x.month, { short: true }))} (${esc(x.reason)})`).join('; ')}.</p>` : '';
    const kept = [p.checked ? `Of them, <b>${p.checked} you checked</b>.` : '', p.chosen ? `<b>${p.chosen} you chose</b> stay as they are.` : ''].filter(Boolean).join(' ');
    // The row being taught is marked among the examples; the flow line then counts the others.
    const mark = x => `${x.source === 'checked' ? ' <span class="prev-mark">checked</span>' : ''}${taught != null && x.id === taught ? ' <span class="prev-mark">this row</span>' : ''}`;
    const rows = p.examples.map(x => `<tr><td class="num">${esc(day(x.date))}</td><td class="r num">${esc(money(x.amount_minor, x.currency))}</td><td>${exampleCell(x, mark(x))}</td></tr>`);
    const others = taught != null && !(p.flows || []).some(f => f.id === taught);
    // See all opens the merchant's page, which lists only its own rows: so only for a change to a
    // merchant's own book or type. A taught wording's rows are another merchant's, or no merchant's yet.
    const ownRows = p.subject.id != null && (kind === 'merge' || (kind === 'merchant' && fieldLines.length
        && !fieldLines.some(c => c.field === 'wording') && !(p.others || []).length));
    // Without See all, the rows the examples leave out are said here: each the flow line names, by
    // date, amount and wording, then how many more the preview does not name.
    const shownIds = new Set(p.examples.map(x => x.id));
    const more = ownRows || kind === 'rule' ? [] : (p.flows || []).filter(f => !shownIds.has(f.id));
    const rest = ownRows || kind === 'rule' ? 0 : Math.max(0, p.count - p.examples.length - more.length);
    const moreHTML = more.length || rest ? `<ul class="sub ex-more">${more.map(f => `<li>${esc([day(f.date), money(f.amount_minor, f.currency), f.description].join(' · '))}</li>`).join('')}${rest ? `<li>and ${plural(rest, 'more row')}</li>` : ''}</ul>` : '';
    // Where the only rows are those you chose by hand, the count says so, not "0 past rows";
    // ticked, it is the count again (tickMoveChosen).
    const hand = (p.flows_chosen || []).length;
    const zero = !p.count && hand ? `No other past rows change. You chose ${plural(hand, 'row')} below by hand; tick to move ${hand === 1 ? 'it' : 'them'} too.` : '';
    // The rows whose flow Apply moves or keeps, on every preview that carries them.
    const body = `${lines ? `<ul class="diff">${lines}</ul>` : ''}
        <p class="q" id="pv-count" data-count="${p.count}" data-with="${p.count_with_chosen ?? p.count}"${zero ? ` data-zero="${esc(zero)}"` : ''}>${zero ? esc(zero) : `This changes ${plural(p.count, 'past row')}`}</p>
        ${warns}${facts}${leftOut}${ruleLines}${flowsLine(p, { others })}${kept ? `<p class="sub">${kept}</p>` : ''}
        ${rows.length ? `<div class="tshell ex-shell">${bandedTable({ caption: 'Examples, newest first', cls: 'ex', columns: [{ label: 'Date' }, { label: 'Amount', cls: 'r' }, { label: 'Now → after' }], rows })}</div>` : ''}
        ${moreHTML}${ownRows ? `<button type="button" class="link" data-act="preview-all" data-id="${esc(p.subject.id)}">See all ${plural(p.count, 'row')} ›</button>` : ''}`;
    const foot = `<button type="button" class="btn ghost" data-act="close-sheet">Cancel</button><span class="sp"></span><button type="button" class="btn" data-act="preview-answer" data-past="only_new">Only new rows</button><button type="button" class="btn primary" id="pv-apply" data-act="preview-answer" data-past="apply" data-count="${p.count}" data-with="${p.count_with_chosen ?? p.count}" data-unit="past row"${zero ? ' data-zero="Save without moving them"' : ''}>${zero ? 'Save without moving them' : `Apply to ${plural(p.count, 'past row')}`}</button>`;
    return { eyebrow, title, body, foot };
}
/** The drift's drawer (fin-surfaces 51, mocked as 21's screen 6): the rows
 *  that differ from their rule, as examples; Leave them writes nothing,
 *  Apply relabels them. No Only new rows: nothing new is being taught. */
function driftParts(p) {
    const one = p.count === 1;
    // Only rows you chose by hand: Apply moves none of them until the box is ticked (tickMoveChosen).
    const zero = !p.count && (p.flows_chosen || []).length ? 'Apply without moving them' : '';
    const rows = p.examples.map(x => `<tr><td class="num">${esc(day(x.date))}</td><td class="r num">${esc(money(x.amount_minor, x.currency))}</td><td>${exampleCell(x)}</td></tr>`);
    const body = `<p class="sub">Rows labelled by a rule or a merchant whose label is not what the rules give now. Rows you checked or chose are not counted.</p>${flowsLine(p)}
        <div class="tshell ex-shell">${bandedTable({ caption: 'Examples, newest first', cls: 'ex', columns: [{ label: 'Date' }, { label: 'Amount', cls: 'r' }, { label: 'Now → after' }], rows })}</div>`;
    const foot = `<button type="button" class="btn ghost" data-act="close-sheet">Leave them</button><span class="sp"></span><button type="button" class="btn primary" id="pv-apply" data-act="preview-answer" data-past="apply" data-count="${p.count}" data-with="${p.count_with_chosen ?? p.count}" data-unit="row"${zero ? ` data-zero="${zero}"` : ''}>${zero || `Apply to ${plural(p.count, 'row')}`}</button>`;
    return { eyebrow: 'Rules', title: `${plural(p.count, 'row')} ${one ? 'differs' : 'differ'} from ${one ? 'its' : 'their'} rule`, body, foot };
}
/** One example's Now → after, as two parts: on a phone the after takes its
 *  own line (styles.css .ex-after). An after that keeps the row's merchant
 *  names it once: "Sample Car Loan · Transport → no book or type". */
function exampleCell(x, mark = '') {
    const now = String(x.now ?? ''), after = String(x.after ?? '');
    const [lead, ...rest] = after.split(' · ');
    const nowParts = now.split(' · ');
    const shown = rest.length && nowParts.length > 1 && nowParts[0] === lead ? rest.join(' · ') : after;
    return `<span class="ex-now">${esc(now)}</span> <span class="ex-after">→ <b>${esc(shown)}</b>${mark}</span>`;
}
/** The rows whose flow Apply changes (every one is in p.flows), by what they
 *  are now and after: "2 spending → a movement · the loan", and where the
 *  move empties their book and type (label_cleared), "with no book or
 *  type"; then each row it keeps, in its own sentence. `others`: a teach
 *  whose own row is not among them, so they are the other rows. Last, the
 *  rows kept only because the operator chose their book or type
 *  (p.flows_chosen, ADR-008), in their own group: each by date, amount and
 *  wording and what it would become, and the box that moves them too,
 *  unticked (tickMoveChosen; Apply sends it, sendPreviewed). */
function flowsLine(p, { others = false } = {}) {
    const pairs = {};
    (p.flows || []).forEach(f => { const k = `${f.now} → ${f.after}${f.label_cleared ? ', with no book or type' : ''}`; pairs[k] = (pairs[k] || 0) + 1; });
    const said = Object.entries(pairs).map(([k, n]) => `${n} ${esc(k)}`).join('; ');
    const hand = p.flows_chosen || [];
    const one = hand.length === 1;
    const HAND_SHOWN = 10;   // then "and N more", as a teach's rows are (the box still names and moves them all)
    const group = hand.length ? `<div class="pv-hand"><p class="cap">You chose these by hand</p>
        <p class="sub">A rule leaves the ${one ? 'row' : 'rows'} whose book or type you chose as ${one ? 'it is' : 'they are'}. ${one ? 'That choice' : 'Those choices'} may be out of date.</p>
        <ul class="sub ex-more">${hand.slice(0, HAND_SHOWN).map(k => `<li>${esc([day(k.date), money(k.amount_minor, k.currency), k.description].join(' · '))}: ${esc(k.now)} → <b>${esc(k.would)}</b>${k.label_cleared ? ', with no book or type' : ''}</li>`).join('')}${hand.length > HAND_SHOWN ? `<li>and ${plural(hand.length - HAND_SHOWN, 'more row')}</li>` : ''}</ul>
        <label class="check"><input type="checkbox" id="pv-move-chosen" data-change="preview-move-chosen"> Also move ${one ? 'this row' : `these ${hand.length} rows`} you chose by hand</label></div>` : '';
    return (said ? `<p class="sub">What the ${others ? 'other ' : ''}rows are: ${said}.</p>` : '')
        + keptWords(p).map(k => `<p class="sub">${esc(k)}</p>`).join('') + group;
}
/** The rows whose flow is kept, a record of expect back resting on each:
 *  each named by date, amount and wording, and why, in one plain sentence
 *  ("6 Feb 2026 · S$ 68.28 · GIRO DEDUCTION AUTO stays spending, because
 *  S$ 5 of it is expected back (undo that mark to move it)."). Only the
 *  drawer says them; a toast counts them (flowCounts). */
function keptWords(p) {
    // The rows kept only because you chose them are flowsLine's own group.
    const hand = new Set((p.flows_chosen || []).map(k => k.id));
    return (p.flows_kept || []).filter(k => !hand.has(k.id)).map(k => {
        const row = [day(k.date), money(k.amount_minor, k.currency), k.description].join(' · ');
        return k.because ? `${row} stays ${k.now}, because ${k.because}.` : `${row} stays ${k.now}. ${k.why}`;
    });
}
/** Ask the preview: 'apply', 'only_new', or null when it is closed. Whether
 *  the box "Also move these rows you chose by hand" was ticked when Apply
 *  was pressed is kept on the preview, p.moveChosen: the answer sends
 *  move_chosen with it (sendPreviewed, the drift's Apply). */
function askPreview(p, parts = previewParts(p)) {
    previewShown = p;
    p.moveChosen = false;
    return new Promise(done => {
        openSheet(parts.title, parts.body, { eyebrow: parts.eyebrow, center: true, foot: parts.foot, focusSheet: true });
        pastAnswer = done;
    });
}
let previewShown = null;
ACT['preview-answer'] = el => {
    if (previewShown) previewShown.moveChosen = el.dataset.past === 'apply' && !!$('#pv-move-chosen')?.checked;
    ACT['past-answer'](el);
};
/** The box ticked or not: the drawer's count and its Apply say the rows Apply then changes. */
function tickMoveChosen(el) {
    const apply = $('#pv-apply'), count = $('#pv-count');
    const n = d => Number(el.checked ? d.with : d.count);
    if (apply) apply.textContent = !el.checked && apply.dataset.zero ? apply.dataset.zero : `Apply to ${plural(n(apply.dataset), apply.dataset.unit || 'past row')}`;
    if (count) count.textContent = !el.checked && count.dataset.zero ? count.dataset.zero : `This changes ${plural(n(count.dataset), 'past row')}`;
}
ACT['preview-move-chosen'] = tickMoveChosen;

// See all: the merchant's own page lists every one of its rows, newest first.
ACT['preview-all'] = el => { closeSheet(); location.hash = `#/books/merchant/${el.dataset.id}`; };
/** A write that may reach past rows: sent once; when the server answers with
 *  a preview, the drawer asks, and the answer goes back (under `key`) with
 *  the preview's token. `parts` draws the drawer in place of previewParts
 *  (a slot move's); `taught` names a teach's own row, which the drawer marks. Returns send's result, or {ok: false, cancelled}
 *  when the drawer was closed: nothing was written. */
async function sendPreviewed(method, url, body, { key = 'past', parts = null, taught = null } = {}) {
    const r = await send(method, url, body);
    if (r.status !== 409 || !r.data.preview) return r;
    const answer = await askPreview(r.data.preview, parts ? parts(r.data.preview) : taught != null ? previewParts(r.data.preview, { taught }) : undefined);
    if (!answer) return { ok: false, cancelled: true, status: 409, data: { error: 'Nothing was saved.' }, change: null };
    const p = r.data.preview;
    // Apply pressed with the box ticked over rows the preview kept because you chose them.
    const move = answer === 'apply' && !!p.moveChosen && (p.flows_chosen || []).length > 0;
    const sent = { ...body, [key]: answer, token: p.token, ...(move ? { move_chosen: true } : {}) };
    const answered = await send(method, url, sent);
    // The drawer's flows go with the answer: Apply writes exactly those, and the toast counts them;
    // with the box ticked, the rows you chose by hand too.
    const moved = sent.move_chosen ? (p.flows_chosen || []).map(k => ({ ...k, after: k.would })) : [];
    return { ...answered, answered: { past: answer, count: sent.move_chosen ? p.count_with_chosen : p.count,
        flows: [...(p.flows || []), ...moved] } };
}
/** What a previewed write did, in words, for its toast. */
function previewedWords(r, done) {
    const a = r.answered;
    if (!a || !a.count) return done;
    if (a.past === 'apply') return `${done}, and ${plural(a.count, 'past row')} changed. One change in Changes, with Undo.`;
    return `${done}. Only new rows take it; the ${plural(a.count, 'past row')} keep theirs, marked checked.`;
}
/** What a write did to the rows' flows, in counts only (each row is the
 *  drawer's): "7 rows are now a movement; 1 row kept as it is". `moved` the
 *  flow lines it wrote, `kept` the rows whose flow it kept; '' when neither. */
function flowCounts(moved, kept) {
    const by = new Map();
    moved.forEach(f => { const w = String(f.after).split(' · ')[0]; by.set(w, (by.get(w) || 0) + 1); });
    const each = [...by].map(([w, n], i) => i ? `${n} ${w}` : `${plural(n, 'row')} ${n === 1 ? 'is' : 'are'} now ${w}`);
    const now = each.length > 1 ? `${each.slice(0, -1).join(', ')} and ${each[each.length - 1]}` : (each[0] || '');
    const still = kept.length ? `${plural(kept.length, 'row')} kept as ${kept.length === 1 ? 'it is' : 'they are'}` : '';
    return [now, still].filter(Boolean).join('; ');
}
/** A previewed write's toast: what it did (previewedWords), or, where it
 *  moved or kept a flow, that in counts (flowCounts): the drawer's flows on
 *  Apply, with `own` (a teach's row taught), and the answer's flows_kept. */
function doneWords(r, done, own = []) {
    const a = r.answered || {};
    const moved = [...own, ...(a.past === 'apply' ? a.flows || [] : [])];
    const kept = r.data?.flows_kept || [];
    if (!moved.length && !kept.length) return previewedWords(r, done);
    const counted = `${sentence(flowCounts(moved, kept))}.`;
    if (a.past === 'only_new') return `${previewedWords(r, done)} ${counted}`;
    // Rows relabelled whose flow neither moved nor was kept are counted too.
    if (a.past === 'apply' && a.count > moved.length + kept.length) return `${done}, and ${plural(a.count, 'past row')} changed. ${counted}`;
    return `${done}. ${counted}`;
}
/** A teach's (resolve's) toast: doneWords, the row taught's own flow (the
 *  answer's flows) counted with the rest. */
function resolvedWords(r, done) {
    return doneWords(r, done, r.data?.flows || []);
}
/** A previewed write's end: false when the drawer was closed, or the write
 *  refused (said in a toast); else its toast, with Undo, counting the rows
 *  whose flow it moved or kept (doneWords). */
function previewedDone(r, done) {
    if (r.cancelled) return false;
    if (!r.ok) { toast(r.data.error || 'That did not work; nothing was changed', { bad: true }); return false; }
    toast(doneWords(r, done), { undo: r.change });
    refreshFrame();
    return true;
}
ACT['each-on'] = async el => {
    const s = await merchantById(Number(el.dataset.id));
    if (!s) { toast('This merchant is no longer in fin', { bad: true }); closeSheet(); return; }
    $$('#sheets .sheet-foot .btn').forEach(b => { b.disabled = true; });
    await checkEachRowOn(s, el.dataset.past);
};
/** past: 'leave' accepts the past rows as they are (checked); 'queue' leaves them waiting. */
async function checkEachRowOn(s, past) {
    const n = s.rows_to_check;
    const r = await act('POST', `/api/services/${s.id}/check-each-row`, { on: true, past },
        past === 'queue' && n ? `${s.name}: Check each row from now on. Its ${plural(n, 'past row')} ${n === 1 ? 'waits' : 'wait'} in the Queue to check.`
            : `${s.name}: Check each row from now on. Its new rows will wait in the Queue.${n ? ` Its ${plural(n, 'past row')} ${n === 1 ? 'is' : 'are'} marked checked, labels unchanged.` : ''}`);
    closeSheet();
    if (r) rerender();
}
// The merchant page (fin-surfaces 11): one merchant's type, usual book and
// setting, then all its rows (newest first, a page at a time), each with a
// type and a book picker that saves at once. Ticked rows take one book in one
// press (Set book, which chooses the book). Every write is 12's bulk label:
// one change, one Undo. The head leaves room for a trend chart, drawing none.
const MP_PAGE = 50;
const merchantPage = { id: null, page: 1, ticks: new Set(), book: '', busy: false, month: null };
/** The merchant page's rows sort on the server, a page at a time; each key is a column the server's
 *  allowlist holds. The tick, Book and state columns do not sort (the allowlist has no book). */
const MP_SORT = { date: { label: 'Date', kind: 'date' }, account: { label: 'Account', kind: 'text' }, description: { label: 'Statement text', kind: 'text' },
    amount: { label: 'Amount', kind: 'money' }, type: { label: 'Type', kind: 'text' } };
SORT_REDRAW.mp = () => { merchantPage.page = 1; rerender(); };
/** The rows narrowed by a trend bar: month is one 'YYYY-MM', or a quarter's months (a list) read as one date range. */
function monthQuery(month) {
    const ms = [].concat(month || []);
    if (!ms.length) return '';
    if (ms.length === 1) return `&month=${ms[0]}`;
    return `&start=${ms[0]}-01&end=${monthEnd(ms[ms.length - 1])}`;
}
ACT['merchant-page'] = el => { location.hash = `#/books/merchant/${el.dataset.id}`; };
/** The merchant page's Account cell: the account's name opens its page. */
function mpAccountCell(x) {
    return x.account_id ? pageLink('account', x.account_id, x.account_name || '') : esc(x.account_name || '');
}
async function viewMerchant(id) {
    if (merchantPage.id !== id) Object.assign(merchantPage, { id, page: 1, ticks: new Set(), book: '', month: null });
    const mp = merchantPage;
    // Every read at once, none waiting on another; the page draws once all are in.
    // A merchant gone (deleted in another tab) answers 404 to its own reads.
    const gone = err => { if (err.status === 404) return null; throw err; };
    const [s, r, list, varies, owing] = await Promise.all([get(`/api/services/${id}`, { fresh: true }).catch(gone), refs(),
        get(`/api/transactions?service_id=${id}&expense_only=true&${new URLSearchParams(sortServer('mp', MP_SORT))}&per_page=${MP_PAGE}&page=${mp.page}${monthQuery(mp.month)}`, { fresh: true }),
        get(`/api/services/${id}/book-varies`, { fresh: true }).catch(gone),
        get(`/api/services/${id}/balances`, { fresh: true }).catch(() => null)]);
    if (!s) {
        return `<div class="mp">${mpCrumbs()}<div class="page-head"><div><h1>Merchant</h1></div></div>
            <div class="notice">${icon('info')}<span>This merchant is no longer in fin. <a class="link" href="#/books/lists/merchants">Go to Lists</a></span></div></div>`;
    }
    afterRender(() => window.mountMerchantTrend?.());
    ACT.__mpRows = new Map(list.transactions.map(x => [x.id, x]));
    const each = !!s.review_each_time;
    const span = s.rows ? `${plural(s.rows, 'row')} · ${s.first_date === s.last_date ? esc(day(s.first_date)) : `${esc(day(s.first_date))} to ${esc(day(s.last_date))}`} · ${plural(s.accounts, 'account')}` : 'No rows yet';
    const off = mp.busy ? ' disabled' : '';
    // A row says only what differs from its merchant: not checked, or which
    // field was checked or chosen (fin-surfaces 52).
    // Money out is the page's default, so only money in carries its mark.
    const rows = list.transactions.map(x => {
        const on = mp.ticks.has(x.id), when = esc(day(x.date)), minor = toMinor(x.amount_sgd, x.currency);
        const mark = x.not_checked ? tag('notchecked', 'not checked') : sourceTagsHTML(x.sources);
        return `<tr${on ? ' class="is-ticked"' : ''} data-tx="${x.id}"><td class="k"><label class="tick"><input type="checkbox" data-change="mp-tick" data-tx="${x.id}"${on ? ' checked' : ''} aria-label="Tick the ${when} row"></label></td>
            <td class="num dt">${when}</td><td class="ac">${mpAccountCell(x)}</td>
            <td class="stext">${esc(x.description)}${rowMark(x.id)}${window.expectRowPill?.(x) || ''}</td>
            <td class="r num am">${x.counted_minor != null && x.counted_minor !== x.amount_minor ? `${minor < 0 ? dirTag('in') + ' ' : ''}${struckAmount(x)}` : minor == null || minor < 0 ? rowAmount(minor, x.currency) : esc(money(minor, x.currency))}</td>
            <td class="ty"><select class="sel sel--quiet" data-change="mp-type" data-tx="${x.id}" aria-label="Type for the ${when} row"${off}>${typeOptions(r.types, x.type_id)}</select></td>
            <td class="bk"><select class="sel sel--quiet" data-change="mp-book" data-tx="${x.id}" aria-label="Book for the ${when} row"${off}>${bookOptions(r.books, x.book, 'Choose book')}</select></td>
            <td class="st">${mark}${window.expectMerchantRowAction?.(x) || ''}</td></tr>`;
    }).join('');
    const notChecked = each ? s.rows_to_check : 0;
    return `<div class="mp">${mpCrumbs()}
    <div class="page-head"><div><h1>${esc(s.name)}</h1>
        <p>${span} · ${plural(s.rule_count, 'rule')}</p>${window.folioSlot?.('party', s.id) || ''}</div>
        <button type="button" class="btn" data-act="merchant" data-id="${s.id}">Edit the merchant</button></div>
    ${varies?.suggest ? `<div class="attn" role="status"><span class="attn__ic">${icon('info')}</span><p>${esc(bookVariesWords(s.name, varies))}</p>
        <div class="attn__act">${bookVariesButtons(s.id)}</div></div>` : ''}
    <section class="card mp-card" aria-label="${esc(s.name)}"><div class="mp-facts">
        <div class="mp-fact"><span class="cap">Type</span><b>${s.type_id ? esc(s.display_type || s.type_name) : tag('nofig', 'no type')}</b></div>
        <div class="mp-fact"><span class="cap">${each ? 'Usual book' : 'Book'}</span><b>${esc(s.book || 'no book')}</b></div>
        <div class="mp-fact"><span class="cap">Rows</span><b class="num">${s.rows || 0}</b>${notChecked ? `<span class="sub">${notChecked} not checked</span>` : ''}</div>
        <div class="mp-fact mp-fact--set"><span class="cap" id="mp-nr">New rows</span>${eachSegHTML(s, 'mp-nr')}</div>
    </div></section>
    ${partyBalancesHTML(s, owing)}
    <section class="card mp-card" aria-labelledby="mp-rows"><div class="card-head"><div><h2 id="mp-rows">Rows</h2><p>${sortOf('mp').key ? '' : 'Newest first. '}What you set is chosen and stays; the rest follows ${esc(s.name)}. Each change is undone in Changes. <a class="link" href="#/books/rows?party=${s.id}&month=all">Its rows on the Rows page</a>, with the rows on accounts linked to it.</p></div>
        ${list.transactions.length ? `<div class="bulk"><span class="bulk__n" id="mp-ticked">${mpTickedWords()}</span>
            <select class="sel" data-change="mp-bulk-book" aria-label="Book for the ticked rows">${bookOptions(r.books, mp.book, 'Choose book')}</select>
            <button type="button" class="btn sm primary" data-act="mp-apply" id="mp-apply"${mpCanApply() ? '' : ' disabled'}>Set book for ticked rows</button></div>` : ''}</div>
        ${list.transactions.length ? `${sortPicker('mp', MP_SORT, { own: 'Newest first' })}<table class="t mp-t"><caption class="sr-only">${esc(s.name)}’s rows</caption>
            <colgroup><col class="c-k"><col class="c-d"><col class="c-ac"><col><col class="c-a"><col class="c-ty"><col class="c-bk"><col class="c-s"></colgroup>
            <thead><tr><th scope="col" class="k"><span class="sr-only">Tick</span></th><th scope="col">${sortHead('mp', 'date', MP_SORT)}</th><th scope="col">${sortHead('mp', 'account', MP_SORT)}</th><th scope="col">${sortHead('mp', 'description', MP_SORT)}</th><th scope="col" class="r">${sortHead('mp', 'amount', MP_SORT)}</th><th scope="col">${sortHead('mp', 'type', MP_SORT)}</th><th scope="col">Book</th><th scope="col"><span class="sr-only">State</span></th></tr></thead>
            <tbody>${rows}</tbody></table>${pager(list, 'mp-page', !!sortOf('mp').key)}`
        : '<p class="card-empty">No spending rows from this merchant yet.</p>'}</section></div>`;
}
/** A party's balances with the household and their net (RUL-020): what we
 *  owe them on a loan joined to them, our money they hold on a shared
 *  account joined to them. Nothing when no account with a balance is joined. */
function partyBalancesHTML(s, owing) {
    if (!owing || !owing.lines.length) return '';
    const side = { owed: 'we owe them', held: 'ours they hold' };
    const lines = owing.lines.map(l => `<tr><td>${pageLink('account', l.account_id, l.name)}<div class="small muted">${esc(side[l.side])}</div></td>
        <td class="r num">${l.balance_minor == null ? tag('nofig', 'no figure') : esc(money(Math.abs(l.balance_minor), l.currency))}</td>
        <td class="r num">${l.value_minor == null ? (l.balance_minor == null ? '' : tag('nofig', 'no rate')) : esc(money(Math.abs(l.value_minor), 'SGD'))}</td></tr>`).join('');
    const out = owing.left_out.length ? `<div class="card-head"><p class="card-foot">Left out of the net: ${owing.left_out.map(x => `${esc(x.name)} (${esc(x.why)})`).join('; ')}.</p></div>` : '';
    return `<section class="card mp-card" aria-labelledby="mp-owing"><div class="card-head"><div><h2 id="mp-owing">Balances with ${esc(s.name)}</h2>
        <p>At ${esc(day(owing.as_at))}: what we owe them, what of ours they hold, and the net.</p></div></div>
        <table class="t"><thead><tr><th scope="col">Account</th><th scope="col" class="r">In its currency</th><th scope="col" class="r">In S$</th></tr></thead>
        <tbody>${lines}</tbody><tfoot><tr><th scope="row" colspan="2">Net</th><td class="r num"><b>${owing.net ? esc(owing.net) : '—'}</b></td></tr></tfoot></table>${out}</section>`;
}
/** Where the page sits: Books › Lists › Merchants (folio's crumbs). */
function mpCrumbs() {
    return `<nav class="crumbs" aria-label="Where"><a href="#/books">Books</a> › <a href="#/books/lists/merchants">Lists</a> › Merchants</nav>`;
}
function mpTickedWords() {
    const n = merchantPage.ticks.size;
    return n ? `<b>${plural(n, 'row')} ticked</b><button type="button" class="btn sm ghost" data-act="mp-untick">Clear</button>`
        : 'Tick rows to set their book together.';
}
const mpCanApply = () => !!(merchantPage.ticks.size && merchantPage.book && !merchantPage.busy);
ACT['mp-page'] = el => { merchantPage.page = Number(el.dataset.page); rerender(); window.scrollTo(0, 0); };
ACT['mp-tick'] = el => {
    const id = Number(el.dataset.tx);
    if (el.checked) merchantPage.ticks.add(id); else merchantPage.ticks.delete(id);
    el.closest('tr').classList.toggle('is-ticked', el.checked);
    $('#mp-ticked').innerHTML = mpTickedWords();
    $('#mp-apply').disabled = !mpCanApply();
};
ACT['mp-untick'] = () => {
    merchantPage.ticks.clear();
    $$('#view .mp-t tr.is-ticked').forEach(tr => { tr.classList.remove('is-ticked'); tr.querySelector('input[type="checkbox"]').checked = false; });
    $('#mp-ticked').innerHTML = mpTickedWords();
    $('#mp-apply').disabled = !mpCanApply();
};
ACT['mp-bulk-book'] = el => { merchantPage.book = el.value; $('#mp-apply').disabled = !mpCanApply(); };
/** Set book for ticked rows is a deliberate act: its book is chosen even
 *  where it is a row's own (personal-ledger 24), so the request says so. */
function mpSetBookBody(ids, book) {
    return { rows: ids.map(tx_id => ({ tx_id, book })), chosen: true };
}
/** One write at a time on the page: bulk label, then the toast and a redraw. */
async function mpLabel(lines, words) {
    if (merchantPage.busy) { toast('One save at a time: wait for the one running to finish.'); return null; }
    merchantPage.busy = true;
    $$('#view .mp-t select, #mp-apply').forEach(b => { b.disabled = true; });
    const r = await send('POST', '/api/transactions/bulk-label', Array.isArray(lines) ? { rows: lines } : lines);
    merchantPage.busy = false;
    if (r.ok) toast(words, { undo: r.change });
    else toast(r.data.error || 'That did not work; nothing was changed', { bad: true, ms: 9000 });
    rerender(); refreshFrame();
    return r.ok ? r : null;
}
ACT['mp-apply'] = async () => {
    const mp = merchantPage, name = $('#view h1')?.textContent || 'this merchant';
    if (!mpCanApply()) return;
    const ids = [...mp.ticks];
    const r = await mpLabel(mpSetBookBody(ids, mp.book), `${mp.book} on ${plural(ids.length, 'row')} of ${name}`);
    if (r) { mp.ticks.clear(); askBookVaries(mp.id); }
};
ACT['mp-type'] = el => {
    const x = ACT.__mpRows.get(Number(el.dataset.tx));
    if (!x || !el.value) return;
    mpLabel([{ tx_id: x.id, type_id: Number(el.value) }], `Set the type of the ${day(x.date)} row to ${el.options[el.selectedIndex].text}.`);
};
ACT['mp-book'] = el => {
    const x = ACT.__mpRows.get(Number(el.dataset.tx));
    if (!x || !el.value) return;
    mpLabel([{ tx_id: x.id, book: el.value }], `Set the book of the ${day(x.date)} row to ${el.value}.`)
        .then(r => { if (r) askBookVaries(merchantPage.id); });
};

// fin suggests Check each row (fin-surfaces 14): once two rows of an Every
// row the same merchant have their book chosen as another, fin asks, after the
// change (a toast that stays until answered) and on the merchant page. Yes
// runs the setting's offer (each-set); No is kept for good, and fin does not
// ask again for that merchant. The server decides (book-varies).
function bookVariesWords(name, v) {
    return `${name}’s book varies: you’ve moved ${v.moved} of its rows to ${v.to_book || 'other books'}. Check each row from now on?`;
}
/** The ask's two answers, as buttons in the toast and on the page alike:
 *  sand Yes, ghost No (the toast puts No first, its Yes at the edge). */
function bookVariesButtons(id, { noFirst = false } = {}) {
    const yes = `<button type="button" class="btn sm subtle" data-act="bv-yes" data-id="${id}" data-on="1">Yes, check each row</button>`;
    const no = `<button type="button" class="btn sm ghost" data-act="bv-no" data-id="${id}">No</button>`;
    return noFirst ? no + yes : yes + no;
}
/** After a row's book changed: ask, if the server says to. */
async function askBookVaries(id) {
    let v, s;
    try { [v, s] = await Promise.all([get(`/api/services/${id}/book-varies`, { fresh: true }), merchantById(id)]); } catch (_) { return; }
    if (!v.suggest || !s) return;
    $$(`#toasts .toast.ask[data-id="${id}"]`).forEach(t => t.remove());
    const box = document.createElement('div');
    box.className = 'toast ask';
    box.dataset.id = id;
    box.setAttribute('role', 'status');
    box.innerHTML = `${icon('info')}<span>${esc(bookVariesWords(s.name, v))}</span><span class="acts">${bookVariesButtons(id, { noFirst: true })}</span>`;
    $('#toasts').appendChild(box);
}
const dropBookVaries = id => $$(`#toasts .toast.ask[data-id="${id}"]`).forEach(t => t.remove());
ACT['bv-yes'] = el => { dropBookVaries(el.dataset.id); ACT['each-set'](el); };
ACT['bv-no'] = async el => {
    const id = Number(el.dataset.id);
    dropBookVaries(id);
    const r = await send('POST', `/api/services/${id}/book-varies/decline`);
    if (!r.ok) { toast(r.data.error || 'That did not work; fin will ask again', { bad: true }); return; }
    const s = await merchantById(id);
    toast(`fin won’t ask again for ${s ? s.name : 'this merchant'}. Its setting stays on its page.`);
    if (location.hash === `#/books/merchant/${id}`) rerender();
};

// Adding, Check each row starts as the picked type's default (type-relationships
// 02, case 7) until the operator ticks or unticks it; after that a new pick
// leaves it as they set it.
let addTypes = new Map();
ACT['mc-type-pick'] = el => {
    const box = $('#mc-each');
    if (box.dataset.touched) return;
    box.checked = !!addTypes.get(Number(el.value))?.check_each_row;
};
ACT['mc-each-touch'] = el => { el.dataset.touched = '1'; };
ACT['merchant-add'] = async () => {
    const r = await refs();
    addTypes = r.typeById;
    openSheet('Add a merchant', `<div class="fields">${merchantFields(r, null)}</div>
        <p class="small muted">A merchant takes rows through its rules: add a rule for it next, from Rules.</p>`,
    { eyebrow: 'Merchants', foot: '<button class="btn primary" data-act="merchant-create">Add the merchant</button>' });
};
ACT['merchant-create'] = async () => {
    const name = $('#mc-name').value.trim();
    if (!name) { toast('Give it a name', { bad: true }); return; }
    const body = { name, book: $('#mc-book').value || null, type_id: $('#mc-type').value ? Number($('#mc-type').value) : null, notes: $('#mc-notes').value.trim() || null,
        review_each_time: $('#mc-each').checked ? 1 : 0, exclude_from_expense_views: $('#mc-hidden').checked ? 1 : 0 };
    const r = await act('POST', '/api/services', body, 'Merchant added');
    if (r) { closeSheet(); rerender(); }
};
// Edit the merchant reads the merchants list itself (cached): Merge into offers every other merchant.
ACT.merchant = async el => {
    const [r, services] = await Promise.all([refs(), servicesList()]);
    const s = services.find(x => x.id === Number(el.dataset.id));
    if (!s) { toast('This merchant is no longer in fin', { bad: true }); return; }
    const others = services.filter(x => x.id !== s.id);
    openSheet(esc(s.name), `<div class="fields">${merchantFields(r, s)}
        <label class="check"><input type="checkbox" id="mc-oneoff"${s.is_one_off ? ' checked' : ''}> One-off</label>
        <p class="small muted">${plural(s.txn_count, 'row')} · ${plural(s.rule_count, 'rule')}${(s.rules || []).length ? ': ' + s.rules.map(x => esc(x.pattern)).join(', ') : ''}. A change of book or type that reaches its past rows is shown first, to apply to them or to new rows only.</p></div>
        <button class="btn primary block" data-act="merchant-save" data-id="${s.id}">Save</button>
        <hr class="rule"><h3>Clean up</h3>
        <div class="sl-merge"><label class="field"><span>Merge into</span><select id="mc-target"><option value="">Choose a merchant</option>${others.map(x => `<option value="${x.id}">${esc(x.name)}</option>`).join('')}</select></label>
            <button class="btn" data-act="merchant-merge" data-id="${s.id}">Merge</button></div>
        <button class="btn danger" data-act="merchant-delete" data-id="${s.id}">${icon('trash')}Delete this merchant</button>`, { eyebrow: 'Merchant' });
};
ACT['merchant-save'] = async el => {
    const body = { name: $('#mc-name').value.trim(), book: $('#mc-book').value || null, type_id: $('#mc-type').value ? Number($('#mc-type').value) : null,
        notes: $('#mc-notes').value.trim() || null,
        exclude_from_expense_views: $('#mc-hidden').checked ? 1 : 0, is_one_off: $('#mc-oneoff').checked ? 1 : 0 };
    const r = await sendPreviewed('PUT', `/api/services/${el.dataset.id}`, body);
    if (r.cancelled) return;
    if (!r.ok) { toast(r.data.error || 'That did not work; nothing was changed', { bad: true }); return; }
    toast(previewedWords(r, 'Merchant saved'), { undo: r.change });
    refreshFrame();
    closeSheet(); rerender();
};
ACT['merchant-merge'] = async el => {
    const target = $('#mc-target').value;
    if (!target) { toast('Choose the merchant to merge into', { bad: true }); return; }
    const r = await sendPreviewed('POST', `/api/services/${el.dataset.id}/merge`, { target_id: Number(target) });
    if (previewedDone(r, 'Merged')) { closeSheet(); afterMerge(Number(el.dataset.id), Number(target)); }
};
/** After a merge: on the merged-away merchant's own page, its page is gone,
 *  so go to the page of the one it merged into; anywhere else, draw again. */
function afterMerge(source, target) {
    if (location.hash === `#/books/merchant/${source}`) location.hash = `#/books/merchant/${target}`;
    else rerender();
}
ACT['merchant-delete'] = async el => {
    if (!confirm('Delete this merchant? You can undo it from Changes.')) return;
    const r = await act('DELETE', `/api/services/${el.dataset.id}`, undefined, 'Merchant deleted');
    if (r) { closeSheet(); rerender(); }
};

/** A rule's Sets cell in Lists › Rules: book, type, amount range and the
 *  flow it states, with the overrides tag. */
function ruleSets(x, r) {
    const book = esc(x.book_override || x.book || '');
    const type = x.type_override_id || x.type_name ? esc(x.type_override_id ? r.typeById.get(x.type_override_id)?.display_name || r.typeById.get(x.type_override_id)?.name : x.display_type || x.type_name) : '';
    const range = x.min_amount !== null || x.max_amount !== null
        ? `only ${x.min_amount !== null ? esc(money(toMinor(x.min_amount), 'SGD')) : 'any amount'} to ${x.max_amount !== null ? esc(money(toMinor(x.max_amount), 'SGD')) : 'any amount'}` : '';
    const states = x.flow ? `${RULE_FLOWS.find(f => f[0] === x.flow)?.[1] || x.flow}${x.other_side_name ? ' · ' + x.other_side_name : ''}` : '';
    // A flow alone overrides nothing: the tag is for the rule's own book or type.
    return [book, type, range, esc(states)].filter(Boolean).join(' · ') + (x.book_override || x.type_override_id ? ' ' + tag('yours', 'overrides') : '');
}
// What a rule may say its rows are (ADR-009): left at the first, fin works it out.
// Short words, as the preview lines say them (relabel_preview.FLOW_WORDS); the picker's hint says the two that need it.
const RULE_FLOWS = [['', 'What fin works out'], ['expense', 'spending'], ['income', 'income'], ['refund', 'a refund'],
    ['transfer', 'a transfer'], ['payment', 'a card payment'], ['movement', 'a movement']];
/** A flow in the app's own words ("a movement", "a card payment"), never its stored name. */
function flowSaid(flow) {
    const found = RULE_FLOWS.find(([k]) => k && k === flow);
    return found ? found[1] : String(flow || '').replace(/_/g, ' ');
}
ACT.rule = async el => {
    const [r, services] = await Promise.all([refs(), servicesList()]);
    const x = el.dataset.id ? ACT.__rules.get(Number(el.dataset.id)) : null;
    openSheet(x ? esc(x.pattern) : 'Add a rule', `<div class="fields">
        <div class="fields two"><label class="field"><span>Pattern</span><input type="text" id="rl-pattern" value="${esc(x?.pattern || '')}"></label>
            <label class="field"><span>Match</span><select id="rl-match">${['contains', 'startswith', 'exact'].map(m => `<option value="${m}"${(x?.match_type || 'contains') === m ? ' selected' : ''}>${MATCH_WORDS[m]}</option>`).join('')}</select></label></div>
        <label class="field"><span>Merchant</span><select id="rl-svc"><option value="">Choose</option>${services.map(s => `<option value="${s.id}"${s.id === x?.service_id ? ' selected' : ''}>${esc(s.name)}</option>`).join('')}</select></label>
        <div class="fields two"><label class="field"><span>Book, in place of the merchant’s</span><select id="rl-book">${bookOptions(r.books, x?.book_override, 'The merchant’s')}</select></label>
            <label class="field"><span>Type, in place of the merchant’s</span><select id="rl-type">${typeOptions(r.types, x?.type_override_id, { blank: 'The merchant’s' })}</select></label></div>
        <div class="fields two"><label class="field"><span>What its rows are</span><select id="rl-flow">${RULE_FLOWS.map(([v, w]) => `<option value="${v}"${(x?.flow || '') === v ? ' selected' : ''}>${esc(w)}</option>`).join('')}</select></label>
            <label class="field"><span>Other side</span><select id="rl-side"><option value="">None</option>${r.accounts.filter(a => a.id === x?.other_side_id || (a.owner === 'Household' && a.type !== 'person' && a.status !== 'archived')).map(a => `<option value="${a.id}"${a.id === x?.other_side_id ? ' selected' : ''}>${esc(a.name)}</option>`).join('')}</select></label></div>
        <p class="hint">A movement’s other side is a loan, holding or company.</p>
        <div class="fields two"><label class="field"><span>Only from (S$)</span><input type="text" inputmode="decimal" id="rl-min" value="${x?.min_amount ?? ''}"></label>
            <label class="field"><span>Only up to (S$)</span><input type="text" inputmode="decimal" id="rl-max" value="${x?.max_amount ?? ''}"></label></div></div>
        ${x ? `<p class="small muted">It labels ${plural(x.rows_labelled ?? 0, 'row')} now.</p>` : ''}
        <p class="small muted">A change that reaches past rows is shown first, to apply to them or to new rows only. Rows you chose keep their labels.</p>`,
    { eyebrow: x ? 'Rule' : 'Rules', foot: `${x ? `<button class="btn danger" data-act="rule-delete" data-id="${x.id}">${icon('trash')}Delete</button>` : ''}<button class="btn primary" data-act="rule-save" data-id="${x ? x.id : ''}">Save</button>` });
};
ACT['rule-save'] = async el => {
    const v = id => $('#' + id).value.trim();
    if (!v('rl-svc')) { toast('Choose the merchant', { bad: true }); return; }
    const body = { pattern: v('rl-pattern'), match_type: v('rl-match'), service_id: Number(v('rl-svc')), book_override: v('rl-book') || null,
        type_override_id: v('rl-type') ? Number(v('rl-type')) : null, min_amount: v('rl-min') || null, max_amount: v('rl-max') || null,
        flow: v('rl-flow') || null, other_side_id: v('rl-flow') && v('rl-side') ? Number(v('rl-side')) : null };
    const r = await sendPreviewed(el.dataset.id ? 'PUT' : 'POST', el.dataset.id ? `/api/rules/${el.dataset.id}` : '/api/rules', body);
    if (previewedDone(r, el.dataset.id ? 'Rule saved' : 'Rule added')) { closeSheet(); rerender(); }
};
// A delete that would relabel past rows asks in the preview; one that would
// not is done at once, with Undo in its toast.
ACT['rule-delete'] = async el => {
    const r = await sendPreviewed('DELETE', `/api/rules/${el.dataset.id}`, {});
    if (previewedDone(r, 'Rule deleted')) { closeSheet(); rerender(); }
};
// Re-running every rule is retired (fin-surfaces 21 and 51). Lists › Rules says
// how many rows differ from what their rule says now (fields from the
// merchant or a rule; never one checked, chosen or from the wording), drawn only
// above 0, and opens the one-confirm drawer: Leave them, or Apply.
/** The drift line in the rules card's head: a quiet ghost button, or nothing at 0. */
function driftLine(d) {
    if (!d || !d.count) return '';
    const one = d.count === 1;
    return `<button type="button" class="btn sm ghost" data-act="rule-drift">${plural(d.count, 'row')} ${one ? 'differs' : 'differ'} from what ${one ? 'its' : 'their'} rule says now ›</button>`;
}
ACT['rule-drift'] = async () => {
    const p = await get('/api/rules/drift', { fresh: true }).catch(() => null);
    if (!p) { toast('fin could not count the rows just now; nothing was changed', { bad: true }); return; }
    if (!p.count && !(p.flows_chosen || []).length) { toast('No row differs from its rule now.'); rerender(); return; }
    if (await askPreview(p) !== 'apply') return;
    const move = !!p.moveChosen && (p.flows_chosen || []).length > 0;
    const r = await send('POST', '/api/rules/drift', { past: 'apply', token: p.token, ...(move ? { move_chosen: true } : {}) });
    closeSheet();
    if (!r.ok) { toast(r.data.error || 'That did not work; nothing was changed', { bad: true }); rerender(); return; }
    toast(`${plural(r.data.recategorized, 'row')} relabelled. One change in Changes, with Undo.`, { undo: r.change });
    refreshFrame();
    rerender();
};

/** An amount in whole minor units as an amount field shows it: grouped as
 *  money() groups it, no sign: "100,000.00". Blank for none. */
function amountField(minor, cur = 'SGD') {
    if (minor === null || minor === undefined) return '';
    return groupFor(cur).format(minor / 10 ** digitsOf(cur));
}
/** A loan's hire-purchase terms on its account sheet (loan_terms.py), as
 *  entered: the flat rate as typed, or the interest over the term. All blank
 *  is no terms. With them, its statement's total of remaining instalments
 *  imports as principal owed, and the amount financed is its first figure,
 *  owed from the day given. `lock` (the account's terms_locked) makes them
 *  read-only, saying why. data-held keeps what the fields first held, so a
 *  save sends the terms only when the operator changed them (termsChange). */
function termsFieldsHTML(t, cur = 'SGD', lock = null) {
    const held = {
        financed: amountField(t?.financed_minor, cur), months: t ? String(t.months) : '',
        flat_rate: t?.flat_rate ?? '',
        interest: t && (t.flat_rate === null || t.flat_rate === undefined) ? amountField(t.interest_minor, cur) : '',
        first_instalment: t?.first_instalment ?? '', owed_from: t?.owed_from ?? '',
    };
    const off = lock ? ' disabled' : '';
    const field = (label, id, key, attrs) => `<label class="field"><span>${label}</span><input ${attrs} id="${id}" value="${esc(held[key])}"${off}></label>`;
    const why = lock ? `Read-only: ${String(lock).replace(/ Nothing was saved\.$/, '')}` : 'With these, a statement’s remaining instalments import as principal owed (Rule of 78). Leave all blank for none.';
    return `<fieldset class="fields hp-terms" id="ac-hp" data-held="${esc(JSON.stringify(held))}"><legend>Hire-purchase terms</legend>
        <p class="hint">${esc(why)}</p>
        ${field('Amount financed', 'ac-hp-financed', 'financed', 'type="text" inputmode="decimal"')}
        ${field('Months', 'ac-hp-months', 'months', 'type="text" inputmode="numeric"')}
        <div class="fields two">${field('Flat rate, % a year', 'ac-hp-rate', 'flat_rate', 'type="text" inputmode="decimal"')}
        ${field('or interest over the term', 'ac-hp-interest', 'interest', 'type="text" inputmode="decimal"')}</div>
        ${field('First instalment', 'ac-hp-first', 'first_instalment', 'type="date"')}
        ${field('Amount financed owed from', 'ac-hp-from', 'owed_from', 'type="date"')}
        ${t ? `<span class="hint">Instalment ${esc(money(t.instalment_minor, cur))}; interest ${esc(money(t.interest_minor, cur))} over the term. The amount financed, owed from ${esc(day(t.owed_from))}, is the loan’s first figure.</span>`
            : '<span class="hint">Owed from: blank is the day before the first instalment’s month.</span>'}</fieldset>`;
}
/** What a save sends for the terms: undefined when the fields say what they
 *  first held (nothing sent), null when all are cleared, else the terms as
 *  typed. */
function termsChange(held, fields) {
    const keys = ['financed', 'months', 'flat_rate', 'interest', 'first_instalment', 'owed_from'];
    const val = (o, k) => String(o?.[k] ?? '').trim();
    if (keys.every(k => val(fields, k) === val(held, k))) return undefined;
    if (keys.every(k => !val(fields, k))) return null;
    const months = val(fields, 'months');
    const out = { financed: val(fields, 'financed').replace(/[, ]/g, ''), months: /^\d+$/.test(months) ? Number(months) : months };
    if (val(fields, 'flat_rate')) out.flat_rate = val(fields, 'flat_rate').replace(/%$/, '').trim();
    if (val(fields, 'interest')) out.interest = val(fields, 'interest').replace(/[, ]/g, '');
    out.first_instalment = val(fields, 'first_instalment');
    if (val(fields, 'owed_from')) out.owed_from = val(fields, 'owed_from');
    out.method = 'rule-of-78';
    return out;
}
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
        ${a ? `<label class="check"><input type="checkbox" id="ac-archived"${a.status === 'archived' ? ' checked' : ''}> Archived: shown apart, still counted</label>` : ''}
        ${a && ['bank', 'card'].includes(a.type) ? `<label class="check"><input type="checkbox" id="ac-shared"${a.shared ? ' checked' : ''}> Shared: someone else’s money is on it too</label>
        <span class="hint">A shared account’s rows are not ours unless a rule says what they are, or the money came from one of our own accounts. Its balance is our part, from a figure you enter; its statements still tie every row.</span>` : ''}</div>
        ${a && a.type === 'loan' ? termsFieldsHTML(a.terms, a.currency, a.terms_locked) : ''}
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
        const body = { name, last_four: $('#ac-last4').value.trim() || null, status: $('#ac-archived').checked ? 'archived' : 'active' };
        // A loan's terms go with the save, in the same change, only when the operator changed them.
        const hp = $('#ac-hp');
        if (hp && !$('#ac-hp-financed').disabled) {
            const now = { financed: $('#ac-hp-financed').value, months: $('#ac-hp-months').value, flat_rate: $('#ac-hp-rate').value,
                interest: $('#ac-hp-interest').value, first_instalment: $('#ac-hp-first').value, owed_from: $('#ac-hp-from').value };
            const change = termsChange(JSON.parse(hp.dataset.held), now);
            if (change !== undefined) body.terms = change;
        }
        r = await act('PUT', `/api/accounts/${el.dataset.id}`, body, 'Account saved');
        // Shared or not is its own change: the rows on the account move with it.
        const shared = $('#ac-shared');
        const held = (await refs()).accountById.get(Number(el.dataset.id));
        if (r && shared && shared.checked !== !!held?.shared) {
            const s = await send('PUT', `/api/accounts/${el.dataset.id}/shared`, { shared: shared.checked });
            if (!s.ok) { toast(s.data.error || 'That did not work; nothing was changed', { bad: true }); r = null; }
            else {
                const kept = s.data.kept.length ? `; ${plural(s.data.kept.length, 'row')} kept by what rests on them` : '';
                const chosen = s.data.chosen ? `; ${plural(s.data.chosen, 'row')} lost the label you chose (Undo puts it back)` : '';
                toast(`${shared.checked ? 'Marked shared' : 'Marked not shared'}: ${plural(s.data.moved, 'row')} moved${kept}${chosen}`, { undo: s.change });
                refreshFrame();
            }
        }
    } else {
        r = await act('POST', '/api/accounts', { name, type: $('#ac-kind').value, owner: $('#ac-owner').value, currency: $('#ac-cur').value, last_four: $('#ac-last4').value.trim() || null }, 'Account added');
    }
    if (r) { closeSheet(); rerender(); }
};

// ---------------------------------------------------------------------------
// Import: three results, each drawn as a sum; a drop strip on the desk
// ---------------------------------------------------------------------------

const importState = { preview: null, done: null, busy: false, showEmpty: false, rowsFrom: '', queue: [], dropped: 0, read: 0, failed: null, seq: 0 };
// A drop is read a group at a time: one request under what the server takes
// (10 MB) and well inside how long the hosting proxy waits. Confirming a
// group reads the next.
const IMPORT_GROUP_FILES = 8;
const IMPORT_GROUP_BYTES = 8 * 1024 * 1024;
const IMPORT_WAIT_MS = 4 * 60 * 1000;
async function viewImport() {
    // refs() beside the page's reads: the coverage grid names each account from window.__accountById, which refs() fills.
    const [, past, coverage] = await Promise.all([refs(), get('/api/import/history').catch(() => []), get('/api/statements/coverage?months=6').catch(() => null)]);
    const p = importState.preview;
    afterRender(wireDrop);
    return `${booksNav('import')}
    <div class="page-head"><div><span class="eyebrow">Books · Import</span><h1>Import</h1><p>Statements go through one tie check: opening balance and the rows must come to the closing balance, exactly. One that does not tie is refused whole.</p></div></div>
    <section class="card import-drop">
        <div class="drop desk-only" id="drop"><span class="drop__icon">${icon('upload-simple')}</span><b>Drop statement files here</b>
            <span>or <label class="link" for="imp-files">choose them</label>. Each goes through the same tie check.</span></div>
        <div class="phone-only"><label class="btn block" for="imp-files">${icon('upload-simple')}Choose statement files</label></div>
        <input type="file" id="imp-files" multiple hidden data-change="import-files">
        <label class="field import-from"><span>Leave out rows before <span class="muted">(optional)</span></span>
            <input type="date" id="imp-rows-from" value="${esc(importState.rowsFrom)}" data-change="import-rows-from"></label>
        ${importState.busy ? `<p class="loading">Reading…${groupWords()}</p>` : ''}
        ${importState.failed && !importState.busy ? `<div class="notice warn">${icon('warning')}<span class="grow">${importState.dropped > importState.failed.length ? `Files ${importState.read - importState.failed.length + 1}–${importState.read} of ${importState.dropped}` : 'These files'} could not be read; nothing was written.${laterWords(false)}</span>
            <button class="btn" data-act="import-retry">Try them again</button>${importState.queue.length ? '<button class="btn" data-act="import-next">Skip to the next files</button><button class="btn" data-act="import-clear">Start again</button>' : ''}</div>` : ''}
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
/** Past imports' sortable columns; What happened and Result sort by their words. */
const PAST_SORT = { when: { label: 'When', kind: 'date', of: b => b.created_at }, accounts: { label: 'Accounts', kind: 'text', of: b => (b.accounts || []).join(', ') },
    rows: { label: 'Rows', kind: 'number', of: b => b.total_lines }, state: { label: 'What happened', kind: 'text', of: b => plainText(pastState(b)) },
    result: { label: 'Result', kind: 'text', of: b => plainText(pastResult(b)) } };
function pastImportsHTML(past) {
    const empty = past.filter(b => !b.total_lines && !(b.accounts || []).length && !(b.statements || []).length);
    const listed = importState.showEmpty ? past : past.filter(b => !empty.includes(b));
    const rows = sortRows('imports', listed, PAST_SORT).map(b => `<tr><td data-label="When">${esc(when(b.created_at))}</td><td data-label="Accounts">${b.accounts.length ? b.accounts.map(esc).join(', ') : '<span class="muted">none read</span>'}</td>
        <td class="r num" data-label="Rows">${b.total_lines}</td><td data-label="What happened">${pastState(b)}</td><td data-label="Result">${pastResult(b)}</td></tr>`).join('');
    return `<section class="card"><div class="section-header"><h2>Past imports</h2><span class="section-header__aside">${plural(past.length - empty.length, 'import')}</span></div>
        ${sortPicker('imports', PAST_SORT, { own: 'Newest first' })}<div class="table-shell"><table class="t cells past-table"><thead><tr>${Object.keys(PAST_SORT).map(k =>
            `<th${k === 'rows' ? ' class="r"' : ''}>${sortHead('imports', k, PAST_SORT)}</th>`).join('')}</tr></thead><tbody>
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
/** " Files 9–16 of 64." while a drop is read in groups; nothing for one group. */
function groupWords() {
    const n = (importState.files || []).length;
    if (importState.dropped <= n) return '';
    return ` Files ${importState.read - n + 1}–${importState.read} of ${importState.dropped}.`;
}
/** What happens to the files after this group. */
function laterWords(offered = true) {
    const n = importState.queue.length;
    if (!n) return '';
    if (!offered) return ` ${plural(n, 'file')} still to read; Start again drops them.`;
    const next = groupSize(importState.queue);
    return ` The next ${next === 1 ? 'file is' : `${next} are`} read when you import these; Start again drops all ${n} still to read.`;
}
ACT['import-next'] = () => { importState.preview = null; return readGroup(nextGroup()); };
/** The next group of a drop: at most IMPORT_GROUP_FILES files and
 *  IMPORT_GROUP_BYTES bytes, and never none (a file over it goes alone). */
function groupSize(queue) {
    let n = 0, bytes = 0;
    while (n < queue.length && n < IMPORT_GROUP_FILES) {
        const size = queue[n].size || 0;
        if (n && bytes + size > IMPORT_GROUP_BYTES) break;
        n += 1; bytes += size;
    }
    return n;
}
function nextGroup() {
    const group = importState.queue.splice(0, groupSize(importState.queue));
    importState.read += group.length;
    return group;
}
// A range statement that starts before the books do comes in from this day
// (tie.from_day). Changed with a preview open, the same files are read again.
ACT['import-rows-from'] = el => {
    importState.rowsFrom = el.value;
    if (importState.preview && importState.files && !importState.busy) readGroup(importState.files);
};
/** A drop: in name order, read a group at a time (nextGroup). */
function uploadFiles(files) {
    if (!files || !files.length) return;
    importState.queue = Array.from(files).sort((a, b) => a.name.localeCompare(b.name));
    importState.dropped = importState.queue.length;
    importState.read = 0;
    importState.done = null;
    return readGroup(nextGroup());
}
async function readGroup(files) {
    // Only the latest read is answered: a drop made while a group is read
    // replaces the first drop whole, and its late answer is let go.
    const mine = ++importState.seq;
    importState.files = files;
    importState.failed = null;
    const sentFrom = importState.rowsFrom;
    const form = new FormData();
    importState.files.forEach(f => form.append('files', f));
    if (sentFrom) form.append('rows_from', sentFrom);
    // The old preview goes while the files are read: nothing is confirmed
    // against a day it was not read with.
    importState.busy = true; importState.preview = null; rerender();
    const r = await send('POST', '/api/import/upload', form, { waitMs: IMPORT_WAIT_MS,
        slow: 'fin took too long to read these files; nothing was written. Send fewer at a time.' });
    if (mine !== importState.seq) return;
    importState.busy = false;
    if (r.status === 413 && files.length > 1) {
        // A group over what the server takes after all: read in halves, the second half next.
        const half = Math.ceil(files.length / 2);
        importState.queue.unshift(...files.slice(half));
        importState.read -= files.length - half;
        return readGroup(files.slice(0, half));
    }
    if (r.status === 413 && files.length === 1) {
        // One file over what fin takes: named, left out, and the rest read on.
        toast(`${files[0].name} is more than fin takes in one go (10 MB); it was left out.`, { bad: true });
        if (importState.queue.length) return readGroup(nextGroup());
        rerender(); return;
    }
    if (!r.ok) {
        // The files still to read stay: this group can be read again or skipped.
        importState.failed = files;
        toast(r.data.error || 'fin could not read those files', { bad: true }); rerender(); return;
    }
    // The day changed while they were read: read them again with it.
    if (importState.rowsFrom !== sentFrom) return readGroup(importState.files);
    importState.preview = r.data;
    rerender();
}
ACT['import-retry'] = () => importState.failed && readGroup(importState.failed);
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
/** A loan statement's figure as a sum. With the loan's hire-purchase terms
 *  the statement's figure is the total of its remaining instalments: plus an
 *  instalment it took off ahead of its day, less the interest not yet earned
 *  (Rule of 78), it is the principal owed, which is what the import writes
 *  (loan_terms.py). */
function loanFigureSum(f) {
    const m = v => esc(money(v, f.currency));
    const held = f.held ? ' <span class="muted">(already held)</span>' : '';
    if (f.printed_minor === undefined || f.printed_minor === null) {
        return `<table class="sum small"><tbody><tr><td class="op"></td><td>owed${held}</td><td class="r">${m(f.owed_minor)}</td></tr></tbody></table>`;
    }
    const schedule = f.left_by_schedule !== f.instalments_left ? `; the terms' dates leave ${f.left_by_schedule}` : '';
    return `<table class="sum small"><tbody>
        <tr><td class="op"></td><td>remaining instalments, as printed</td><td class="r">${m(f.printed_minor)}</td></tr>
        ${f.ahead_minor ? `<tr><td class="op">+</td><td>the next instalment, taken off before it is due<div class="small muted">still owed on this day</div></td><td class="r">${m(f.ahead_minor)}</td></tr>` : ''}
        <tr><td class="op">−</td><td>interest not yet earned<div class="small muted">Rule of 78: ${f.instalments_left} of ${f.months} instalments left${schedule}</div></td><td class="r">${m(f.unearned_minor)}</td></tr>
        <tr class="eq"><td class="op">=</td><td>principal owed${held}</td><td class="r">${m(f.owed_minor)}</td></tr></tbody></table>`;
}
function importPreviewHTML(p) {
    const ties = p.groups.filter(g => g.tie === 'ties');
    const unchecked = p.groups.filter(g => g.tie !== 'ties' && g.total);
    const refused = p.errors.filter(e => e.tie);
    const unread = p.errors.filter(e => !e.tie);
    const rows = p.groups.reduce((a, g) => a + g.transactions.length, 0);
    // A loan statement has no rows: what it states is owed is the loan's figure.
    const figures = p.figures || [];
    const newFigures = figures.filter(f => !f.held).length;
    const count = (list, test) => list.filter(test).length;
    const lo = p.rows_from;
    const leftOut = lo ? `<div class="notice">Rows before ${esc(day(lo.day))} are left out: ${plural(lo.rows, 'row')}${lo.statements ? `, ${plural(lo.statements, 'statement')} wholly before it` : ''}. A statement over the day comes in from it, opening on the balance of the day before.</div>` : '';
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
    const figuresHTML = figures.length ? card('figure', tag('aside', 'Loan figure'), figures.length, figures.map(f => stmtBlock(f.account,
        `loan statement ${esc(day(f.date))}: no rows${f.new_account ? '; the loan account is made' : ''}`,
        loanFigureSum(f))).join('')) : '';
    const offered = [rows ? plural(rows, 'row') : '', newFigures ? plural(newFigures, 'figure') : ''].filter(Boolean).join(' and ');
    const groupsHTML = p.groups.filter(g => g.transactions.length).map(g => `<details class="card import-group"><summary><b>${esc(g.account)}</b> · ${plural(g.transactions.length, 'row')} · ${g.typed} with a type, ${g.untyped} without</summary>
        <div class="table-shell" style="margin-top:10px"><table class="t rows acct-rows"><thead><tr><th>Date</th><th>Description</th><th>Labelled</th><th class="r">Money in</th><th class="r">Money out</th></tr></thead><tbody>
        ${g.transactions.map(t => {
            const minor = toMinor(t.amount_sgd, g.currency);
            return `<tr><td class="num row-date">${esc(day(t.date, { year: false }))}</td><td class="row-desc">${esc(t.description)}</td><td class="row-label">${t.flow_type === 'review' ? tag('nofig', 'will wait for a label') : t.type_name ? esc(t.type_name) : t.flow_type !== 'expense' ? `<span class="muted">${esc(flowSaid(t.flow_type))}</span>` : tag('nofig', 'no type')}</td>
            <td class="r num row-in">${minor < 0 ? esc(money(-minor, g.currency)) : ''}</td><td class="r num row-out">${minor >= 0 ? esc(money(minor, g.currency)) : ''}</td></tr>`;
        }).join('')}</tbody></table></div></details>`).join('');
    return `${leftOut}<div class="results">${card('ties', tag('ties', 'Ties'), ties.reduce((a, g) => a + g.statements.length, 0), tiesBody)}${card('off', tag('off', 'Off by'), refused.length, refusedBody)}${card('notchecked', tag('notchecked', 'Not checked'), unchecked.length, uncheckedBody)}</div>
        ${figuresHTML}
        ${unread.length ? `<div class="notice bad import-unread">${icon('warning-circle')}<span>${unread.map(e => `${esc(e.file)}: ${esc(e.error)}`).join('<br>')}</span></div>` : ''}
        ${offered ? `<section class="card import-bar"><p>${offered} ready. A refused statement stays in the queue until a file that ties is imported.${groupWords()}${laterWords()}</p>
            <div class="row"><button class="btn" data-act="import-clear">Start again</button><button class="btn primary" data-act="import-confirm">Import ${offered}</button></div></section>${groupsHTML}`
            : `<section class="card import-bar"><p>Nothing to import.${groupWords()}${laterWords(false)}</p><div class="row"><button class="btn" data-act="import-clear">Start again</button>${importState.queue.length ? '<button class="btn primary" data-act="import-next">Read the next files</button>' : ''}</div></section>`}`;
}
/** The choices a confirm may carry for one previewed row (app.py
 *  _CONFIRM_ROW_CHOICES); its facts stay on the server. */
const IMPORT_CHOICES = ['book', 'type_id', 'service_id', 'flow_type', 'other_side_id', '_skip', 'is_one_off'];
function importChoices(t) {
    const out = {};
    IMPORT_CHOICES.forEach(k => { if (k in t) out[k] = t[k]; });
    return out;
}
ACT['import-clear'] = () => { importState.seq += 1; importState.busy = false; importState.preview = null; importState.done = null; importState.files = null; importState.failed = null; importState.queue = []; importState.dropped = importState.read = 0; rerender(); };
ACT['import-confirm'] = async el => {
    const p = importState.preview;
    const at = importState.seq;
    el.disabled = true;
    const r = await send('POST', '/api/import/confirm', {
        import_id: p.import_id,
        // Choices only: the facts are the preview the server kept (main's
        // facts-free confirm); every group, in order, each row by position.
        groups: p.groups.map(g => ({ account: g.account, transactions: g.transactions.map(importChoices) })),
    });
    if (!r.ok) { el.disabled = false; toast(r.data.error || 'Nothing was imported', { bad: true }); return; }
    // A drop made while it was confirmed has the page now: it was imported, and the page is the new drop's.
    if (at !== importState.seq) { toast('Imported', { undo: r.change }); return; }
    importState.preview = null; importState.files = null;
    const figuresDone = r.data.figures_written ? ` and ${plural(r.data.figures_written, 'figure')}` : '';
    const held = r.data.duplicates_skipped ? `; ${plural(r.data.duplicates_skipped, 'row')} already held ${r.data.duplicates_skipped === 1 ? 'was' : 'were'} not added again` : '';
    importState.done = `Imported ${plural(r.data.transactions_saved || 0, 'row')}${figuresDone}${held}. It is one change in Changes, with Undo.`;
    toast('Imported', { undo: r.change });
    if (importState.queue.length) return readGroup(nextGroup());
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

// A field's source is shown by fin-surfaces 52's marks, not as a changed field.
const SKIP_FIELDS = new Set(['id', 'created_at', 'book_source', 'type_source', 'flow_type_manual', 'imported_at', 'fetched_at', 'updated_at', 'statement_id', 'printed']);
const FIELD_WORDS = { type_id: 'type', service_id: 'merchant', other_side_id: 'other side', flow_type: 'kind', is_one_off: 'one-off', notes: 'note',
    amount_minor: 'amount', account_id: 'account', type_override_id: 'type (rule)', book_override: 'book (rule)', review_each_time: 'check each row',
    exclude_from_expense_views: 'hidden', match_type: 'match', min_amount_minor: 'from', max_amount_minor: 'up to', amount: 'amount', statement_date: 'statement', covers: 'covers', not_for: 'not for',
    usual_book: 'usual book', check_each_row: 'Check each row by default', default_one_off: 'one-off by default',
    party_id: 'party', role: 'role' };
// What fin files through each slot, as the server says it (book_type.SLOTS).
const SLOT_FILES = { loan_interest: 'loan interest', gift_given: 'gifts given', gift_received: 'gifts received' };
// An empty old or new value said plainly, never as bad news (C4.2).
const EMPTY_WORDS = { type_id: 'no type', type_override_id: 'no type', service_id: 'no merchant', other_side_id: 'none', notes: 'empty',
    party_id: 'no party', role: 'no role' };
const FLOW_WORDS = { expense: 'money out', income: 'money in', refund: 'a refund', review: 'waiting for a label', transfer: 'a move between own accounts', not_ours: 'not ours' };
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
    // An account's party is a merchant (fin-redesign 09), named as one.
    if (field === 'service_id' || field === 'party_id') {
        const gone = ctx.gone?.get(value);
        return esc(ctx.services.find(s => s.id === value)?.name || (gone ? `${gone}, since removed` : 'a merchant no longer listed'));
    }
    if (['is_one_off', 'review_each_time', 'exclude_from_expense_views', 'check_each_row', 'default_one_off'].includes(field)) return value ? 'yes' : 'no';
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
/** The merchants a change's own rows name (an undone save that made one:
 *  its after, the undo's before), by id: a merchant gone from fin is still
 *  named on the change's row lines. */
function goneMerchants(changes) {
    const out = new Map();
    for (const c of changes) {
        if (c.table !== 'services') continue;
        const row = c.after || c.before || {};
        if (row.name) out.set(c.row_id, row.name);
    }
    return out;
}
function rowName(c) {
    const row = c.after || c.before || {};
    return row.description || c.display_name || row.name || row.pattern || row.pair || '';
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
    const xw = window.expectChangeWords?.(c, ctx, { rows, subject }); if (xw) return xw;  // expect back's seven tables (expect-mark.js)
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
    // An account joined to a party, or taken off one (fin-redesign 09), short
    // enough for one line on a phone: "joined Sample Co to Sample Co Health (party)".
    if (c.table === 'accounts' && changedFields(c).join() === 'party_id') {
        return isEmpty(a.party_id) ? `took ${name} off ${valueText('accounts', 'party_id', b.party_id, ctx)} (party)`
            : `joined ${name} to ${valueText('accounts', 'party_id', a.party_id, ctx)} (party)`;
    }
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
    // A twin taken out says so, and which twin stays (fin RUL-026), not only "Took out …".
    if (String(e.summary || '').startsWith('Removed a duplicate row:')) return esc(e.summary);
    return sentence(changeWords(e.first, ctx, { rows: e.rows })) || esc(e.summary || 'A change');
}
/** Why an undo is refused, naming who and what (walk C6.2), said as an
 *  explanation: the later change comes off first, then this one. */
function refusalWords(e, ctx) {
    const by = e.blocked_by;
    const did = changeWords(by.first, ctx, { rows: 1, subject: 'it' });
    const target = e.rows === 1 ? 'this row' : 'the same rows';
    const what = did && by.first && by.first.table === 'transactions' && e.rows === 1
        ? `${whoName(by, { cap: false })} ${did} ${timeWords(by.at)}`
        : `${esc(lowerFirst(String(by.summary || 'a change')))} ${timeWords(by.at)}`;
    return `${whoName(by)} changed ${target} after it: ${what}. Undo that first, then this one.`;
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
function writeSwitchHTML(state, h) {
    const on = !!state.enabled;
    const sw = `<button class="switch" role="switch" aria-checked="${on}" data-act="claude-switch" aria-label="Claude may write">
        <span class="track" aria-hidden="true"></span><span class="switch-word">${on ? 'On' : 'Off'}</span></button>`;
    if (!on) {
        return `<section class="card write-card is-off" aria-labelledby="write-head">
            <div class="write-row">
                <span class="write-icon" aria-hidden="true">${icon('prohibit')}</span>
                <div class="write-text"><h2 id="write-head">Claude may not write</h2>
                    <p>Claude can still read fin and answer in chat. Every write from chat is refused and changes nothing.</p>
                    ${state.unreadable ? '<p class="write-since">The switch could not be read, so writes are off. Turning it on saves it again.</p>'
                        : state.changed_at ? `<p class="write-since">Off since ${esc(when(state.changed_at))}.</p>` : ''}</div>
                <div class="write-end">${sw}<button class="btn" data-act="claude-switch-on">Turn it back on</button></div>
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
    const [h, writes, r, services] = await Promise.all([get('/api/history?limit=300&blockers=1', { fresh: true }), chatWrites(), refs(), servicesList()]);
    const ctx = { r, services };
    if (!S.onChanges) {
        // The divider stays where the mark was when the page was opened; opening it is looking.
        S.onChanges = true;
        S.lastSeenMark = h.last_looked;
        S.lastSeenAt = h.last_looked_at;
        if (h.newest && h.newest !== h.last_looked) {
            send('POST', '/api/changes/looked', { upto: h.newest }, { keep: true }).then(() => refreshFrame());
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
                    ${e.blocked_by ? `<div class="notice refusal">${icon('info')}<div><b>Waits on a later change.</b> ${refusalWords(e, ctx)}
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
    ${writeSwitchHTML(writes, h)}
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
/** Turn Claude's writes on or off, asked first both ways; the page then
 *  shows the server's answer, never its own guess. */
function setClaudeWrite(on) {
    openSheet(on ? 'Let Claude write?' : 'Turn off Claude’s writes?',
        on ? `<p>Claude may then change fin through its chat tools: labels, notes, figures, rules, merchants and rates. Every change lands in Changes and can be undone.</p>
              <p class="muted">Only you can turn this on; the chat cannot.</p>`
           : `<p>Every write Claude tries will be refused and change nothing. Claude can still read fin.</p>
              <p class="muted">Only you can turn it back on, here.</p>`,
        { eyebrow: 'Claude may write', center: true,
          foot: `<button class="btn" data-act="close-sheet">Cancel</button><button class="btn primary" data-act="claude-switch-go" data-on="${on ? 1 : 0}">${on ? 'Turn on' : 'Turn off'}</button>` });
}
ACT['claude-switch-go'] = async el => {
    const on = el.dataset.on === '1';
    el.disabled = true;
    const r = await send('PUT', '/api/chat-writes', { enabled: on });
    closeSheet();
    if (!r.ok) toast(r.data.error || 'The switch could not be saved; it is unchanged.', { bad: true });
    else toast(r.data.enabled ? 'Claude may write again.' : 'Claude’s writes are off. Reads still work.');
    rerender();
};
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

function rowLabel(table, row, ctx, shown = null) {
    if (!row) return esc(table);
    // A type is named as every screen names it (Kids > Childcare): the server reads its parent.
    if (table === 'types') return `type ${esc(shown || row.name)}`;
    if (table === 'books') return `book ${esc(row.name)}`;
    // A type's settings and fin's own filings (type-relationships 06), named as Lists › Types names them.
    if (table === 'type_settings') return `type ${valueText(table, 'type_id', row.type_id, ctx) || 'no longer listed'}`;
    if (table === 'type_slots') return `what fin files as ${esc(SLOT_FILES[row.slot] || String(row.slot).replace(/_/g, ' '))}`;
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
    const changes = entry.changes;
    const ctx = { r: await refs(), services: await servicesList(), gone: goneMerchants(changes) };
    // A slot may file under an income kind: those are named too.
    if (changes.some(c => c.table === 'type_slots')) {
        const income = await get('/api/types?kind=income').catch(() => []);
        ctx.r = { ...ctx.r, typeById: new Map([...ctx.r.typeById, ...income.map(t => [t.id, t])]) };
    }
    const out = [];
    const txRows = new Set(changes.filter(c => c.table === 'transactions').map(c => c.row_id));
    // A changed row sorts by its name, account or amount; its field lines stay together under it.
    const cols = { row: { label: 'Row', kind: 'text', of: c => rowName(c) }, account: { label: 'Account', kind: 'text', of: c => accountOf(c, ctx) },
        amount: { label: 'Amount', kind: 'money', of: c => { const row = c.after || c.before || {}; return c.table === 'transactions' && (c.currency || 'SGD') === 'SGD' && !isEmpty(row.amount_minor) ? -row.amount_minor : null; } } };
    for (const c of sortRows('change-rows', changes, cols).slice(0, 60)) {
        const row = c.after || c.before;
        const name = c.table === 'transactions' && txRows.size > 1
            ? `<button class="link" data-act="row" data-tx="${c.row_id}">${esc(row.description)}</button> <span class="muted">· ${esc(day(row.date, { year: false }))}</span>`
            : rowLabel(c.table, row, ctx, c.display_name);
        const acct = esc(accountOf(c, ctx));
        const amt = c.table === 'transactions' ? esc(txAmount(c)) : c.table === 'anchors' ? valueText('anchors', 'amount', row.amount, ctx, c.currency) : '';
        const lines = c.op === 'update' ? changedFields(c).map(k => [esc(FIELD_WORDS[k] || k.replace(/_/g, ' ')), beforeCell(c, k, ctx), afterCell(c, k, ctx)])
            // An added type shows the wording it came with: what Jev is sent.
            : c.op === 'insert' && c.table === 'types' ? ['covers', 'not_for'].filter(k => !isEmpty(row[k])).map(k => [esc(FIELD_WORDS[k]), '<span class="empty-val">not there</span>', afterCell(c, k, ctx)])
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
    const head = k => sortHead('change-rows', k, cols);
    return `${sortPicker('change-rows', cols)}<div class="table-shell"><table class="t table-tight diff-table"><thead><tr><th>${head('row')}</th><th class="c-acct">${head('account')}</th><th>What changed</th><th>Before</th><th>After</th><th class="r c-amt">${head('amount')}</th></tr></thead>
        <tbody>${out.join('')}</tbody></table></div>${more}${open}`;
}
SORT_REDRAW['change-rows'] = () => S.changes.open.forEach(id => fillEntry(id));
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
/** A toast's Undo over several changes: newest first, as the batch undo
 *  below; it stops at the first one refused and says so. */
async function undoChanges(ids) {
    const list = [...ids].sort((a, b) => b - a);
    if (list.length === 1) return undoEntry(list[0]);
    let done = 0;
    for (const id of list) {
        const r = await send('POST', `/api/history/${id}/undo`);
        if (!r.ok) { toast(`Undid ${done} of ${list.length}. One was refused: ${r.data.error || ''}`, { bad: true, ms: 10000 }); break; }
        done += 1;
    }
    if (done === list.length) toast(`Undid ${plural(done, 'change')}. Each undo is in Recent changes, and can itself be undone.`);
    refreshFrame(); rerender();
}
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

const LABEL_FIELDS = ['type_id', 'service_id', 'flow_type', 'book'];
/** Who labelled a row and when, from its own history (walk C8.2); '' for a
 *  row whose book or type has a source, which sourceLinesHTML says per field
 *  (spec 24: one line per field on the row sheet). With neither and no label
 *  change in its history: by hand when its kind was set by hand, not yet for
 *  a row still waiting or untyped, else the rules, which worked out its kind. */
function labelledBy(found, details) {
    if (found.sources && (found.sources.book || found.sources.type)) return '';
    for (const d of details) {
        if (d.undone_by) continue;
        const c = d.changes.find(x => x.table === 'transactions' && x.row_id === found.id && x.op === 'update');
        if (c && changedFields(c).some(k => LABEL_FIELDS.includes(k))) return `${whoName(d)}${d.undoes ? ', by an undo' : ''}, ${when(d.at)}`;
    }
    if (found.flow_type_manual) return 'by hand';
    const waiting = found.flow_type === 'review' || (!found.type_id && ['expense', 'refund'].includes(found.flow_type));
    return waiting ? 'not yet' : 'the rules';
}

/** A history time's day in the viewer's own time: "12 Oct 2026". */
function historyDay(utc) {
    if (!utc) return '—';
    const t = new Date(/[zZ]|[+-]\d\d:\d\d$/.test(utc) ? utc : utc.replace(' ', 'T') + 'Z');
    if (Number.isNaN(t.getTime())) return day(utc);
    return `${t.getDate()} ${MONTHS[t.getMonth()]} ${t.getFullYear()}`;
}
// Where a row's book and type came from (fin-surfaces 52; spec 24's marks). The
// server gives each field's source and, for checked or chosen, the change entry
// that set it so (set_by); these only word them.
const SOURCE_WORDS = { wording: 'from its wording', derived: 'worked out from figures' };
/** The merchant page's state column: a dashed tag for a field checked, a solid one for chosen, nothing for one fin gave. */
function sourceTagsHTML(sources) {
    const tags = [['book', sources?.book], ['type', sources?.type]].filter(([, src]) => src === 'checked' || src === 'chosen')
        .map(([field, src]) => `<span class="src-tag${src === 'chosen' ? ' is-chosen' : ''}">${field} ${src}</span>`);
    return tags.length ? `<span class="srcs">${tags.join('')}</span>` : '';
}
/** One field's source in words: "from IndigoAir", "checked by you, 9 Oct 2026; follows IndigoAir",
 *  "chosen by Claude in chat, 12 Oct 2026 · see the change", "chosen, before fin kept a history". */
function sourceLineWords(source, by, merchant) {
    const name = merchant ? esc(merchant) : '';
    if (source === 'merchant') return name ? `from ${name}` : 'from its merchant';
    if (source === 'rule') return name ? `from a rule for ${name}` : 'from a rule';
    if (source !== 'checked' && source !== 'chosen') return SOURCE_WORDS[source] || esc(source);
    const said = by ? `${source} by ${whoName(by, { cap: false })}${by.via === 'chat' ? ' in chat' : ''}, ${esc(historyDay(by.at))}`
        : `${source}, before fin kept a history`;
    if (source === 'checked') return name ? `${said}; follows ${name}` : said;
    return by ? `${said} · <button type="button" class="link" data-act="goto-change" data-entry="${Number(by.entry_id)}">see the change</button>` : said;
}
/** The row sheet's source lines: one per field that has a value. */
function sourceLinesHTML(found) {
    const sources = found.sources || {}, by = found.set_by || {};
    const lines = [['Book', 'book'], ['Type', 'type']].filter(([, key]) => sources[key])
        .map(([cap, key]) => `<li><span class="cap">${cap}</span> ${sourceLineWords(sources[key], by[key], found.service_name)}</li>`);
    return lines.length ? `<ul class="srcline" aria-label="Where the book and type came from">${lines.join('')}</ul>` : '';
}

/** The row sheet's line under the figure: direction, date and the account, which opens its page. */
function rowSheetSubHTML(found, minor) {
    const account = found.account_name ? ' · ' + (found.account_id ? pageLink('account', found.account_id, found.account_name) : esc(found.account_name)) : '';
    return `${minor > 0 ? 'Money out' : 'Money in'} · ${esc(day(found.date))}${account}`;
}
/** The row sheet's facts. The type opens its page only for a row that has one (`label` is the plain words otherwise);
 *  a row with no merchant shows "none yet", unlinked. */
function rowFactsHTML(found, label, by) {
    const typed = found.type_id && found.display_type && found.flow_type !== 'review' ? pageLink('type', found.type_id, found.display_type) : label;
    return `<dl class="facts">
            <div><dt>Book</dt><dd>${esc(found.book || '—')}</dd></div>
            <div><dt>Type</dt><dd>${typed}${notCheckedTag(found)}</dd></div>
            ${found.other_side_name ? `<div><dt>Other side</dt><dd>${esc(found.other_side_name)}</dd></div>` : ''}
            <div><dt>Merchant</dt><dd>${found.service_name && found.service_id ? pageLink('merchant', found.service_id, found.service_name) : '<span class="muted">none yet</span>'}</dd></div>
            ${by ? `<div><dt>Labelled by</dt><dd>${esc(by)}</dd></div>` : ''}
        </dl>`;
}

/** A row's twins (fin RUL-026): the other rows on its account, day and amount, each with the
 *  statement it came in on, what that suggests, and Remove when the server allows it. '' for none. */
function twinNoticeHTML(set, txId) {
    if (!set) return '';
    const me = set.rows.find(r => r.id === txId);
    const others = set.rows.filter(r => r.id !== txId);
    const on = r => `the statement ${esc(day(r.statement.from))} – ${esc(day(r.statement.to))}`;
    const listed = others.map(r => `<li><button type="button" class="link" data-act="row" data-tx="${r.id}">#${r.id}</button> ${esc(r.description)}, on ${on(r)}</li>`).join('');
    const action = me && !me.refusal
        ? `<button class="btn sm" data-act="remove-duplicate" data-tx="${txId}">Remove this row as a duplicate</button>`
        : `<p class="small muted">${esc(me?.refusal || '')}</p>`;
    return `<div class="notice warn twin-notice">${icon('warning')}<div>
        <p><b>${others.length === 1 ? 'A twin' : `${others.length} twins`}</b> on the same account, day and amount. This row is on ${on(me)}.</p>
        <ul class="twin-list">${listed}</ul>
        <p class="small">${esc(set.reading)}</p>${action}</div></div>`;
}
ACT['remove-duplicate'] = async el => {
    if (!confirm('Remove this row as a duplicate? Its twin stays. You can undo it from Changes.')) return;
    const r = await act('POST', `/api/transactions/${Number(el.dataset.tx)}/remove-duplicate`, undefined, 'Duplicate removed; its twin stays');
    if (r) { closeSheet(); rerender(); }
};

// One row: its amount, its facts, its note, the one-off toggle, "This was…", and its history.
ACT.row = el => openRowSheet(Number(el.dataset.tx));
// Expect back's reserved entry points (personal-ledger 23): a button with one of these data-acts opens a drawer an expect-*.js shell fills.
ACT['expect-mark'] = el => window.openExpectMark?.(Number(el.dataset.tx), el);
ACT['back-for'] = el => window.openBackFor?.(Number(el.dataset.tx), el);
ACT['let-go'] = el => window.openLetGo?.(Number(el.dataset.id), el);
async function openRowSheet(txId, opts = {}) {
    openSheet('A row', '<p class="loading">Loading…</p>', { eyebrow: 'One row' });
    const found = (await get(`/api/transactions?tx_id=${txId}`, { fresh: true })).transactions[0];
    if (!found) { sheetBody().innerHTML = '<p class="notice">This row is no longer in the books (an import was undone, perhaps).</p>' + await timelineHTML('transactions', txId, opts); return; }
    ACT.__lastRow = found;
    const [held, twins] = await Promise.all([rowHistory('transactions', txId),
        get(`/api/duplicates?tx_id=${txId}`, { fresh: true }).catch(() => ({ twins: [] }))]);
    const waiting = found.flow_type === 'review';
    const untyped = !found.display_type && ['expense', 'refund'].includes(found.flow_type);
    const label = waiting ? tag('nofig', 'waiting for a label') : found.display_type ? esc(found.display_type) : (untyped ? tag('nofig', 'no type') : esc(FLOW_WORDS[found.flow_type] || found.flow_type));
    const minor = toMinor(found.amount_sgd, found.currency);
    $('#sheet-title').textContent = found.description;
    // "This was…" is the row's one main action only while it waits for a label (walk C8.3).
    const thisWas = waiting || untyped
        ? `<button class="btn" data-act="${waiting ? 'this-was' : 'resolve'}" data-tx="${found.id}">This was…</button>`
        : `<button class="link" data-act="resolve" data-tx="${found.id}">Change what this was…</button>`;
    sheetBody().innerHTML = `<div class="row-figure-block">
            <div class="row-figure num">${esc(money(-minor, found.currency, { signed: true }))}</div>
            <p class="row-figure-sub">${rowSheetSubHTML(found, minor)}</p></div>
        ${twinNoticeHTML(twins.twins[0], found.id)}
        ${rowFactsHTML(found, label, labelledBy(found, held.details))}
        ${sourceLinesHTML(found)}
        ${window.folioSlot?.('row', found.id) || ''}
        <label class="check"><input type="checkbox" data-change="row-oneoff" data-tx="${found.id}"${found.is_one_off ? ' checked' : ''}> One-off <span class="muted small">(Spending can leave it out)</span></label>
        <label class="field"><span>Note</span><textarea id="row-note">${esc(found.notes || '')}</textarea></label>
        <div class="row row-actions"><button class="btn" data-act="row-note" data-tx="${found.id}">Save the note</button>${thisWas}${window.expectRowActions?.(found) || ''}</div>
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
