import tensorflow as tf
from tensorflow.contrib import rnn
from tensorflow.contrib import legacy_seq2seq

from collections import defaultdict
import numpy as np

class Model():
	def __init__(self, args, reverse_input, infer=False):
		if reverse_input:
			self.start_token = '<EOF>'
			self.end_token = '<START>'
		else:
			self.start_token = '<START>'
			self.end_token = '<EOF>'

		self.unk_token = '<UNK>'

		self.args = args
		if infer:
			args.batch_size = 1
			args.seq_length = 1

		if args.model == 'rnn':
			cell_fn = rnn.BasicRNNCell
		elif args.model == 'gru':
			cell_fn = rnn.GRUCell
		elif args.model == 'lstm':
			cell_fn = rnn.BasicLSTMCell
		else:
			raise Exception("model type not supported: {}".format(args.model))

		cell = cell_fn(args.rnn_size, state_is_tuple=True)

		self.cell = cell = rnn.MultiRNNCell([cell] * args.num_layers, state_is_tuple=True)

		self.input_data = tf.placeholder(tf.int32, [args.batch_size, args.seq_length])
		self.targets = tf.placeholder(tf.int32, [args.batch_size, args.seq_length])
		self.initial_state = cell.zero_state(args.batch_size, tf.float32)

		with tf.variable_scope('rnnlm'):
			softmax_w = tf.get_variable("softmax_w", [args.rnn_size, args.vocab_size])
			softmax_b = tf.get_variable("softmax_b", [args.vocab_size])
			with tf.device("/cpu:0"):
				embedding = tf.get_variable("embedding", [args.vocab_size, args.rnn_size])
				inputs = tf.split(tf.nn.embedding_lookup(embedding, self.input_data), args.seq_length, 1)
				inputs = [tf.squeeze(input_, [1]) for input_ in inputs]

		def loop(prev, _):
			prev = tf.matmul(prev, softmax_w) + softmax_b
			prev_symbol = tf.stop_gradient(tf.argmax(prev, 1))
			return tf.nn.embedding_lookup(embedding, prev_symbol)

		outputs, last_state = legacy_seq2seq.rnn_decoder(inputs, self.initial_state, cell, loop_function=loop if infer else None, scope='rnnlm')
		output = tf.reshape(tf.concat(outputs, 1), [-1, args.rnn_size])
		self.logits = tf.matmul(output, softmax_w) + softmax_b
		self.probs = tf.nn.softmax(self.logits)
		loss = legacy_seq2seq.sequence_loss_by_example([self.logits],
			[tf.reshape(self.targets, [-1])],
			[tf.ones([args.batch_size * args.seq_length])],
			args.vocab_size)
		self.cost = tf.reduce_sum(loss) / args.batch_size / args.seq_length
		self.final_state = last_state
		self.lr = tf.Variable(0.0, trainable=False)
		tvars = tf.trainable_variables()
		grads, _ = tf.clip_by_global_norm(tf.gradients(self.cost, tvars),
			args.grad_clip)
		optimizer = tf.train.AdamOptimizer(self.lr)
		self.train_op = optimizer.apply_gradients(zip(grads, tvars))


	def sample(self, sess, tokens, vocab, max_tokens = 500, sampling_type=1):
		state = sess.run(self.cell.zero_state(1, tf.float32))
		cur_tok = self.start_token
		ret = ""

		def weighted_pick(weights):
			t = np.cumsum(weights)
			s = np.sum(weights)
			return(int(np.searchsorted(t, np.random.rand(1)*s)))

		for i in range(max_tokens):
			x = np.zeros((1, 1))
			x[0, 0] = vocab[cur_tok]
			feed = {self.input_data: x, self.initial_state:state}
			[probs, state] = sess.run([self.probs, self.final_state], feed)
			p = probs[0]

			if sampling_type == 0:
				sample = np.argmax(p)
			elif sampling_type == 2:
				if token == ' ':
					sample = weighted_pick(p)
				else:
					sample = np.argmax(p)
			else: # sampling_type == 1 default:
				sample = weighted_pick(p)

			pred = tokens[sample]
			if pred == self.end_token:
				break

			ret += pred + " "
			cur_tok = pred
		return ret


	def evaluate(self, sess, tokens, vocab, token_list):
		token_probs = []
		state = sess.run(self.cell.zero_state(1, tf.float32))
		total_entropy = 0
		for n in range(len(token_list)-1):
			x = np.zeros((1, 1))
			x[0, 0] = vocab[token_list[n]]
			feed = {self.input_data: x, self.initial_state:state}
			[probs, state] = sess.run([self.probs, self.final_state], feed)
			prob_dist = probs[0]
			prob_next_token = prob_dist[vocab[token_list[n+1]]]
			entropy_next_token = -1.0 * np.log2(prob_next_token)
			total_entropy += entropy_next_token
			token_probs.append(prob_next_token)
			print("Current token: {0}".format(token_list[n]))
			print("Next token: {0}, Entropy: {1}, Prob: {2}".format(token_list[n+1], entropy_next_token, prob_next_token))
			print("Predicted next token: {0}, Prob: {1}\n".format(tokens[np.argmax(prob_dist)], np.max(prob_dist)))
		print("Average entropy: {0}".format(total_entropy / (len(token_list) - 1)))
		return token_probs[:-1]