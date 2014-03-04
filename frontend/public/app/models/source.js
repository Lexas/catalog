
define(function(require){

	var Backbone = require('lib/backbone');
	var Source = Backbone.Model.extend({
		urlRoot: '', /* ToDo: get /user or /work path */
		changesMade: '0',
		initialize: function(){
			var self = this;
			this.on('change', function(){
				self.changesMade++;
			});
			return;
		}
	});
	return Source;
})