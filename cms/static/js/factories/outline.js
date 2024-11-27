define([
    'js/views/pages/course_outline', 'js/models/xblock_outline_info'
], function(CourseOutlinePage, XBlockOutlineInfo) {
    'use strict';

    return function(XBlockOutlineInfoJson, initialStateJson, initialUserClipboardJson, user_timezoneJson) {
        var courseXBlock = new XBlockOutlineInfo(XBlockOutlineInfoJson, {parse: true}),
            view = new CourseOutlinePage({
                el: $('#content'),
                model: courseXBlock,
                initialState: initialStateJson,
                initialUserClipboard: initialUserClipboardJson,
                user_timezone: user_timezoneJson,
            });
        view.render();
    };
});
