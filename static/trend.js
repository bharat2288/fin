// The trend pages' shared namespace. Holds only window.Trend and the mode registry;
// trend-bars.js and trend-line.js register their drawers here.
(function () {
    const modes = {};
    window.Trend = window.Trend || {};
    window.Trend.modes = modes;
    window.Trend.registerMode = function registerMode(name, draw) { modes[name] = draw; };
})();
